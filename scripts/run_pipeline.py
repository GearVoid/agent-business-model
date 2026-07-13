"""Run discovery, enrichment, and rendering as a fail-fast pipeline."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BASE = SCRIPTS.parent

import discover_industry  # noqa: E402
import discover_papers  # noqa: E402
import enrich_metadata  # noqa: E402
import feed_dedup  # noqa: E402
import image_renderer  # noqa: E402
import text_renderer  # noqa: E402


def atomic_write_json(path: Path, data: object) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def run_step(label: str, module, child_args: list) -> bool:
    print(f"\n=== {label} ===")
    saved = sys.argv
    sys.argv = [f"{module.__name__}.py"] + list(child_args)
    try:
        rc = module.main()
    except Exception as exc:  # no later stage may consume a stale feed
        print(f"[FAIL] {label} raised {type(exc).__name__}: {exc}")
        return False
    finally:
        sys.argv = saved
    if rc not in (None, 0):
        print(f"[FAIL] {label} exited {rc}")
        return False
    return True


def cross_feed_dedup() -> bool:
    """Remove items duplicated between fresh paper and industry feeds."""
    papers = BASE / "feed-papers.json"
    industry = BASE / "feed-industry.json"
    if not papers.exists() or not industry.exists():
        print("[FAIL] cross-feed dedup requires both fresh feed files")
        return False
    paper_feed = json.loads(papers.read_text(encoding="utf-8"))
    industry_feed = json.loads(industry.read_text(encoding="utf-8"))
    kept, removed = feed_dedup.dedup_industry(industry_feed.get("items", []), paper_feed.get("items", []))
    if removed:
        industry_feed["items"] = kept
        industry_feed["count"] = len(kept)
        industry_feed["cross_feed_dups_removed"] = len(removed)
        atomic_write_json(industry, industry_feed)
        print(f"[OK] removed {len(removed)} cross-feed duplicate industry items")
    else:
        print("[OK] cross-feed dedup: no duplicates")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="perovskite-scout pipeline")
    parser.add_argument("--rebuild", action="store_true", help="replace discovery dedup state after successful scans")
    parser.add_argument("--ignore-state", action="store_true", help="run discovery without reading or writing dedup state")
    args = parser.parse_args()
    discover_args = (["--rebuild"] if args.rebuild else []) + (["--ignore-state"] if args.ignore_state else [])

    # Deliberately sequential: returning immediately means a failed upstream
    # source can never produce a newly rendered digest from an old feed.
    for label, module, child_args in (
        ("1/5 discover_papers", discover_papers, discover_args),
        ("2/5 enrich_metadata", enrich_metadata, []),
        ("3/5 discover_industry", discover_industry, discover_args),
    ):
        if not run_step(label, module, child_args):
            print("\n[FAIL] pipeline stopped before rendering; stale feeds were not rendered")
            return 1
    try:
        if not cross_feed_dedup():
            print("\n[FAIL] pipeline stopped before rendering")
            return 1
    except Exception as exc:
        print(f"[FAIL] cross-feed dedup raised {type(exc).__name__}: {exc}")
        return 1
    for label, module in (("4/5 text_renderer", text_renderer), ("5/5 image_renderer", image_renderer)):
        if not run_step(label, module, []):
            print("\n[FAIL] pipeline stopped")
            return 1
    print("\n[OK] pipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
