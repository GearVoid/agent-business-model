"""Offline transport tests for locking and strict webhook delivery semantics."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import deliver  # noqa: E402


class DeliveryTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.delivery_dir = self.root / "delivery"
        self.papers = self.root / "feed-papers.json"
        self.industry = self.root / "feed-industry.json"
        self.state_papers = self.root / "state-feed.json"
        self.state_industry = self.root / "state-industry.json"
        self.digest = self.root / "digest.txt"
        self.compact = self.root / "compact.txt"
        self.card = self.root / "card.png"
        self.state_papers.write_text('{"seen": ["before"]}', encoding="utf-8")
        self.state_industry.write_text('{"seen": ["before"]}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def workspace_patches(self):
        return (
            patch.object(deliver, "DELIVERY_DIR", self.delivery_dir),
            patch.object(deliver, "FEED_PAPERS", self.papers),
            patch.object(deliver, "FEED_INDUSTRY", self.industry),
            patch.object(deliver, "STATE_PAPERS", self.state_papers),
            patch.object(deliver, "STATE_INDUSTRY", self.state_industry),
            patch.object(deliver, "STATE_PATHS", (self.state_papers, self.state_industry)),
            patch.object(deliver, "DIGEST", self.digest),
            patch.object(deliver, "COMPACT_DIGEST", self.compact),
            patch.object(deliver, "CARD_PNG", self.card),
        )

    def pipeline_with_content(self):
        self.papers.write_text('{"items": [{"id": "paper-1"}]}', encoding="utf-8")
        self.industry.write_text('{"items": []}', encoding="utf-8")
        self.state_papers.write_text('{"seen": ["advanced"]}', encoding="utf-8")
        self.state_industry.write_text('{"seen": ["advanced"]}', encoding="utf-8")
        self.digest.write_text("full", encoding="utf-8")
        self.compact.write_text("compact", encoding="utf-8")
        self.card.write_bytes(b"png")
        return 0

    def invoke_webhook(self, urlopen_error, *extra_args):
        with ExitStack() as stack:
            for workspace_patch in self.workspace_patches():
                stack.enter_context(workspace_patch)
            stack.enter_context(
                patch.object(deliver.run_pipeline, "main", side_effect=self.pipeline_with_content)
            )
            stack.enter_context(patch.object(deliver.validate_outputs, "main", return_value=0))
            urlopen = stack.enter_context(
                patch.object(deliver.urllib.request, "urlopen", side_effect=urlopen_error)
            )
            stack.enter_context(
                patch.dict("os.environ", {"DELIVERY_WEBHOOK": "https://receiver.invalid/hook"}, clear=False)
            )
            stack.enter_context(
                patch.object(sys, "argv", ["deliver.py", "--transport", "webhook", *extra_args])
            )
            result = deliver.main()
        manifest = json.loads(
            (self.delivery_dir / "delivery-manifest.json").read_text(encoding="utf-8")
        )
        return result, manifest, urlopen

    def test_held_lock_fails_without_running_pipeline_or_writing_manifest(self):
        with ExitStack() as stack:
            for workspace_patch in self.workspace_patches():
                stack.enter_context(workspace_patch)
            pipeline = stack.enter_context(patch.object(deliver.run_pipeline, "main"))
            lock = deliver.DeliveryLock(deliver.delivery_lock_path(), 60)
            lock.acquire()
            try:
                with patch.object(sys, "argv", ["deliver.py", "--mode", "preview"]):
                    self.assertEqual(deliver.main(), 1)
            finally:
                lock.release()

        pipeline.assert_not_called()
        self.assertFalse((self.delivery_dir / "delivery-manifest.json").exists())

    def test_expired_lock_is_recovered_with_new_metadata(self):
        self.delivery_dir.mkdir()
        lock_path = self.delivery_dir / deliver.LOCK_FILENAME
        lock_path.write_text(
            json.dumps({"pid": 99, "started_at": "old", "expires_at_epoch": 0}),
            encoding="utf-8",
        )

        lock = deliver.DeliveryLock(lock_path, 60)
        lock.acquire()
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["pid"], __import__("os").getpid())
        self.assertIn("started_at", metadata)
        self.assertGreater(metadata["expires_at_epoch"], 0)
        lock.release()

    def test_delivery_id_ignores_discovery_timestamps(self):
        with ExitStack() as stack:
            for workspace_patch in self.workspace_patches():
                stack.enter_context(workspace_patch)
            self.papers.write_text(
                '{"generated_at": "first", "items": [{"id": "p1"}]}',
                encoding="utf-8",
            )
            self.industry.write_text('{"generated_at": "first", "items": []}', encoding="utf-8")
            first = deliver.compute_delivery_id("production")
            self.papers.write_text(
                '{"generated_at": "retry", "items": [{"id": "p1"}]}',
                encoding="utf-8",
            )
            self.assertEqual(first, deliver.compute_delivery_id("production"))

    def test_webhook_5xx_fails_rolls_back_state_and_sends_idempotency_key(self):
        error = urllib.error.HTTPError("https://receiver.invalid/hook", 503, "down", {}, None)
        result, manifest, urlopen = self.invoke_webhook(error)

        self.assertEqual(result, 1)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["reason"], "webhook_delivery_failed")
        self.assertTrue(manifest["delivery_id"].startswith("dly_"))
        self.assertEqual(self.state_papers.read_text(encoding="utf-8"), '{"seen": ["before"]}')
        self.assertFalse((self.delivery_dir / "message.txt").exists())
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["delivery_id"], manifest["delivery_id"])
        self.assertEqual(request.get_header("Idempotency-key"), manifest["delivery_id"])

    def test_webhook_timeout_fails_and_rolls_back_state(self):
        result, manifest, _ = self.invoke_webhook(TimeoutError("timed out"))

        self.assertEqual(result, 1)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(self.state_industry.read_text(encoding="utf-8"), '{"seen": ["before"]}')
        self.assertFalse((self.delivery_dir / "card.png").exists())

    def test_explicit_local_fallback_remains_failed_and_keeps_payload(self):
        result, manifest, _ = self.invoke_webhook(
            TimeoutError("timed out"), "--allow-local-fallback"
        )

        self.assertEqual(result, 1)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["remote_delivery_status"], "failed")
        self.assertTrue(manifest["local_fallback_available"])
        self.assertTrue((self.delivery_dir / "message.txt").exists())

    def test_missing_webhook_url_fails_before_pipeline_and_preserves_state(self):
        pipeline = Mock(return_value=0)
        with ExitStack() as stack:
            for workspace_patch in self.workspace_patches():
                stack.enter_context(workspace_patch)
            stack.enter_context(patch.object(deliver.run_pipeline, "main", pipeline))
            stack.enter_context(patch.dict("os.environ", {}, clear=True))
            stack.enter_context(patch.object(sys, "argv", ["deliver.py", "--transport", "webhook"]))
            self.assertEqual(deliver.main(), 1)

        pipeline.assert_not_called()
        manifest = json.loads(
            (self.delivery_dir / "delivery-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["reason"], "webhook_url_missing")
        self.assertEqual(self.state_papers.read_text(encoding="utf-8"), '{"seen": ["before"]}')


if __name__ == "__main__":
    unittest.main()
