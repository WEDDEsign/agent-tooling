"""A clean review carries across a base update that leaves the PR diff unchanged."""
import copy
import unittest
from unittest.mock import patch

from controller import Controller
from policy import BOT, REPING
from test_review_first import CONFIG, HEAD, NEXT, FakeAPI

FILES = [{"filename": "app.py", "status": "modified", "sha": "1" * 40,
          "patch": "@@ -1,3 +1,4 @@\n a\n+b\n c\n d"}]


def summary(head=HEAD):
    return {"id": 40, "user": {"login": BOT}, "updated_at": "2026-10-10T10:05:00Z",
            "body": "<!-- codex-pull-request-review-summary -->\n"
                    f"| **Code Review** | **Completed** | `{head[:7]}` | PR opened |"}


class CarryAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.pull["changed_files"] = len(FILES)
        self.files = copy.deepcopy(FILES)
        self.discussion = [summary()]
        self.reviews, self.inline = [], []
        self.reactions = [{"user": {"login": BOT}, "content": "+1", "created_at": "2026-10-10T10:05:01Z"}]

    def comments(self, _):
        return copy.deepcopy(self.discussion)

    def pages(self, path, key=None):
        if path.endswith("/reactions"):
            return copy.deepcopy(self.reactions) if path.startswith("issues/comments/") else []
        if path == "pulls/1/reviews":
            return copy.deepcopy(self.reviews)
        if path == "pulls/1/comments":
            return copy.deepcopy(self.inline)
        return super().pages(path, key)

    def update_base(self):
        """Merge the base branch in: new head, new base, identical PR diff."""
        self.pull["head"]["sha"] = NEXT
        self.pull["base"]["sha"] = "d" * 40


class CarryoverTests(unittest.TestCase):
    def setUp(self):
        self.api = CarryAPI()
        self.controller = Controller(self.api, None, "pilot", CONFIG)

    def reviewed_then_updated(self):
        state = {"opened_head": HEAD}
        self.assertEqual(self.controller.review(self.api.pr(1), state, self.api.comments(1)), "clean")
        self.assertEqual(state["reviewed"]["head"], HEAD)
        self.api.update_base()
        return state

    def verdict(self, state):
        return self.controller.review(self.api.pr(1), state, self.api.comments(1))

    def test_base_update_with_identical_diff_keeps_the_review(self):
        state = self.reviewed_then_updated()
        self.assertEqual(self.verdict(state), "clean")
        self.assertEqual(state["reviewed"]["head"], HEAD, "The carried review stays anchored to its head")

    def test_changed_diff_needs_a_fresh_review(self):
        state = self.reviewed_then_updated()
        self.api.files[0]["patch"] += "\n+conflict resolution"
        self.assertEqual(self.verdict(state), "missing")

    def test_unlisted_files_never_carry(self):
        state = self.reviewed_then_updated()
        self.api.pull["changed_files"] = 3001
        self.assertEqual(self.verdict(state), "missing")

    def test_withdrawn_original_review_does_not_carry(self):
        state = self.reviewed_then_updated()
        self.api.discussion.clear()
        self.assertEqual(self.verdict(state), "missing")

    def test_later_finding_on_the_original_head_does_not_carry(self):
        state = self.reviewed_then_updated()
        self.api.inline.append({"user": {"login": BOT}, "commit_id": HEAD,
                                "created_at": "2026-10-10T10:20:00Z", "updated_at": "2026-10-10T10:20:00Z"})
        self.assertEqual(self.verdict(state), "missing")

    def test_codex_activity_on_the_new_head_governs(self):
        state = self.reviewed_then_updated()
        self.api.inline.append({"user": {"login": BOT}, "commit_id": NEXT,
                                "created_at": "2026-10-10T10:20:00Z", "updated_at": "2026-10-10T10:20:00Z"})
        self.assertEqual(self.verdict(state), "missing")

    def test_newly_armed_gate_still_requests_a_review(self):
        state = self.reviewed_then_updated()
        self.api.pull["labels"].append({"name": REPING})
        self.api.events = [{"id": 7, "event": "labeled", "label": {"name": REPING}}]
        self.assertEqual(self.verdict(state), "missing")

    def test_reconcile_runs_final_validation_without_another_review_round(self):
        self.controller.reconcile(1)
        self.api.finish()
        self.controller.reconcile(1)  # initial passed; clean review starts final
        self.api.finish()
        self.controller.reconcile(1)
        self.assertEqual(self.api.gates[-1][1], "success")
        self.api.update_base()
        dispatched = len(self.api.started)
        with patch("controller.time.sleep"):
            self.controller.reconcile(1)
        self.assertEqual(self.api.data["phase"], "final")
        self.assertEqual(len(self.api.started), dispatched + 2, "Final validation reruns on the new head")
        self.assertFalse([n for n in self.api.notices if "review" in str(n.get("body", ""))],
                         "No Codex review request for an unchanged diff")
        self.api.finish()
        self.controller.reconcile(1)
        self.assertEqual(self.api.gates[-1], (NEXT, "success", "Current review and final validation passed"))


if __name__ == "__main__":
    unittest.main()
