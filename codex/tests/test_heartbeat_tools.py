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
SWEEP_PATH = ROOT / ".github" / "workflows" / "sweep-stalled-repings.yml"
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
    def test_claim_rechecks_head_and_latest_checks_before_trigger(self):
        workflow = CI_GREEN_PATH.read_text(encoding="utf-8")
        claim = workflow.index("Claim the gate (remove awaiting-codex-reping)")
        delete = workflow.index("gh api -X DELETE", claim)
        reread = workflow.index(
            'after=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}"', delete
        )
        check_reread = workflow.index("have_after=$(gh api", reread)
        latest_attempt = workflow.index(
            "group_by(.name) | map(max_by(.id))", check_reread
        )
        restore = workflow.index(
            'restore_and_stand_down "required checks are no longer green',
            latest_attempt,
        )
        trigger = workflow.index("Re-ping Codex (bare trigger, posted directly)", restore)

        self.assertLess(delete, reread)
        self.assertLess(reread, check_reread)
        self.assertLess(check_reread, latest_attempt)
        self.assertLess(latest_attempt, restore)
        self.assertLess(restore, trigger)

    def test_event_claim_rejects_terminal_pr_before_trigger(self):
        workflow = CI_GREEN_PATH.read_text(encoding="utf-8")
        claim = workflow.index("Claim the gate (remove awaiting-codex-reping)")
        terminal = workflow.index('pr_state}" != "open"', claim)
        trigger = workflow.index("Re-ping Codex (bare trigger, posted directly)", terminal)

        self.assertLess(terminal, trigger)

    def test_event_transport_rejects_drafts_before_and_after_claim(self):
        workflow = CI_GREEN_PATH.read_text(encoding="utf-8")
        gate = workflow.index("Check label and required-check status")
        claim = workflow.index("Claim the gate (remove awaiting-codex-reping)")
        trigger = workflow.index("Re-ping Codex (bare trigger, posted directly)")
        pre_claim = workflow.index('is_draft=$(gh api', gate)
        post_claim = workflow.index('draft_now=$(printf', claim)

        self.assertLess(pre_claim, claim)
        self.assertLess(claim, post_claim)
        self.assertLess(post_claim, trigger)

    def test_sweep_claim_rechecks_terminal_state_and_latest_checks(self):
        workflow = SWEEP_PATH.read_text(encoding="utf-8")
        claim = workflow.index("# Claim the gate.")
        terminal = workflow.index('pr_state}" != "open"', claim)
        check_reread = workflow.index("runs_after=$(gh api", terminal)
        latest_attempt = workflow.index(
            "group_by(.name) | map(max_by(.id))", check_reread
        )
        trigger = workflow.index('gh pr comment "${pr}"', latest_attempt)

        self.assertLess(terminal, check_reread)
        self.assertLess(check_reread, latest_attempt)
        self.assertLess(latest_attempt, trigger)

    def test_sweep_rejects_drafts_before_and_after_claim(self):
        workflow = SWEEP_PATH.read_text(encoding="utf-8")
        initial_read = workflow.index('is_draft=$(printf')
        claim = workflow.index("# Claim the gate.")
        post_claim = workflow.index('draft_now=$(printf', claim)
        trigger = workflow.index('gh pr comment "${pr}"', post_claim)

        self.assertLess(initial_read, claim)
        self.assertLess(claim, post_claim)
        self.assertLess(post_claim, trigger)


if __name__ == "__main__":
    unittest.main()
