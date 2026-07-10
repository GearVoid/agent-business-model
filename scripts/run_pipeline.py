"""run_pipeline.py — MVP 一键管线: 论文 + 行业 + 渲染。

用法:
  python scripts/run_pipeline.py [--rebuild] [--ignore-state]

说明:
  - 依次执行:
      1/5 discover_papers   (arXiv 抓取 + 过滤 + 去重)
      2/5 enrich_metadata   (Crossref/OpenAlex 补 DOI/OpenAlex ID)
      3/5 discover_industry (行业门户/专业媒体 RSS)
          -> 跨 feed 去重: 剔除与论文重复的 industry 条目
      4/5 text_renderer     (digest.txt + 产业动态区)
      5/5 image_renderer    (card.png/html + 产业动态区)
  - renderer 各自在写入前清理旧的 digest/card 分页产物, 避免投递错文件
  - image_renderer 需 Pillow 出 PNG; 缺 Pillow 时退回 HTML (不卡住)
  - 全链路不调用 LLM
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BASE = Path(__file__).resolve().parent.parent

import discover_papers  # noqa: E402
import enrich_metadata  # noqa: E402
import discover_industry  # noqa: E402
import feed_dedup  # noqa: E402
import text_renderer  # noqa: E402
import image_renderer  # noqa: E402


def run_step(label: str, module, child_args: list) -> bool:
    print(f"\n=== {label} ===")
    saved = sys.argv
    sys.argv = [f"{module.__name__}.py"] + list(child_args)
    try:
        rc = module.main()
    finally:
        sys.argv = saved
    if rc not in (None, 0):
        print(f"[FAIL] {label} exited {rc}")
        return False
    return True


def cross_feed_dedup() -> bool:
    """从 feed-industry.json 剔除与 feed-papers.json 重复的条目, 并重写。"""
    papers = BASE / "feed-papers.json"
    industry = BASE / "feed-industry.json"
    if not papers.exists() or not industry.exists():
        print("[SKIP] 跨 feed 去重: feed 不全, 跳过")
        return True
    pf = json.loads(papers.read_text(encoding="utf-8"))
    ind = json.loads(industry.read_text(encoding="utf-8"))
    kept, removed = feed_dedup.dedup_industry(ind.get("items", []), pf.get("items", []))
    if removed:
        ind["items"] = kept
        ind["count"] = len(kept)
        ind["cross_feed_dups_removed"] = len(removed)
        industry.write_text(json.dumps(ind, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 跨 feed 去重移除 {len(removed)} 条 industry 重复 "
              f"({', '.join(r for _, r in removed)})")
    else:
        print("[OK] 跨 feed 去重: 无重复")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="perovskite-scout MVP pipeline")
    ap.add_argument("--rebuild", action="store_true", help="清空 state 重新生成")
    ap.add_argument("--ignore-state", action="store_true", help="忽略去重")
    args = ap.parse_args()

    discover_args = []
    if args.rebuild:
        discover_args.append("--rebuild")
    if args.ignore_state:
        discover_args.append("--ignore-state")

    ok = True
    ok &= run_step("1/5 discover_papers (arXiv + 过滤 + 去重)", discover_papers, discover_args)
    ok &= run_step("2/5 enrich_metadata (Crossref/OpenAlex 补字段)", enrich_metadata, [])
    ok &= run_step("3/5 discover_industry (行业门户/媒体 RSS)", discover_industry, discover_args)
    if ok:
        ok &= cross_feed_dedup()
    ok &= run_step("4/5 text_renderer (digest.txt)", text_renderer, [])
    ok &= run_step("5/5 image_renderer (card.png/html)", image_renderer, [])

    print("\n" + ("[OK] 管线完成" if ok else "[FAIL] 管线存在失败步骤"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
