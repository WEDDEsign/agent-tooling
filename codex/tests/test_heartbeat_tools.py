#!/usr/bin/env python3
"""Regression tests for the Codex PR-heartbeat assets."""

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]
SYNC_PATH = ROOT / "codex" / "sync-pr-review-heartbeat.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "wake-on-codex-review.yml"
CI_GREEN_PATH = ROOT / ".github" / "workflows" / "wake-on-ci-green.yml"
LABELS_PATH = ROOT / "labels" / "labels.json"

spec = importlib.util.spec_from_file_location("sync_pr_review_heartbeat", SYNC_PATH)
sync_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(sync_module)


class SyncHeartbeatTests(unittest.TestCase):
    def test_new_file_is_byte_idempotent_without_leading_blank_lines(self):
        first, first_action = sync_module.sync_content("")
        second, second_action = sync_module.sync_content(first)

        self.assertEqual("created", first_action)
        self.assertEqual("updated", second_action)
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
        self.assertTrue(first.startswith(sync_module.START))

    def test_existing_crlf_file_keeps_crlf_and_is_byte_idempotent(self):
        prefix = "# Repository\r\n\r\nLocal instructions.\r\n"
        first, first_action = sync_module.sync_content(prefix)
        second, second_action = sync_module.sync_content(first)

        self.assertEqual("appended to", first_action)
        self.assertEqual("updated", second_action)
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
        self.assertTrue(first.startswith(prefix.rstrip("\r\n")))
        self.assertNotIn("\n", first.replace("\r\n", ""))


class OwnershipGuardTests(unittest.TestCase):
    def test_claude_wake_skips_both_codex_owner_signals(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "!startsWith(github.event.pull_request.head.ref, 'codex/')", workflow
        )
        self.assertIn(
            "!contains(github.event.pull_request.labels.*.name, 'codex-only')",
            workflow,
        )

    def test_fallback_owner_label_is_seeded(self):
        taxonomy = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
        labels = {label["name"]: label for label in taxonomy["labels"]}

        self.assertIn("codex-only", labels)
        self.assertEqual("10A37F", labels["codex-only"]["color"])


class CiGreenClaimTests(unittest.TestCase):
    def test_claim_rechecks_head_and_restores_gate_before_trigger(self):
        workflow = CI_GREEN_PATH.read_text(encoding="utf-8")
        claim = workflow.index("Claim the gate (remove awaiting-codex-reping)")
        delete = workflow.index("gh api -X DELETE", claim)
        reread = workflow.index(
            'after=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}"', delete
        )
        restore = workflow.index('-f "labels[]=awaiting-codex-reping"', reread)
        trigger = workflow.index("Re-ping Codex (bare trigger, posted directly)", restore)

        self.assertLess(delete, reread)
        self.assertLess(reread, restore)
        self.assertLess(restore, trigger)


if __name__ == "__main__":
    unittest.main()
