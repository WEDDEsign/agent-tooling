import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from controller import Controller
from main import admit, main, verify_checkout
from policy import (ACTIVE, BOT, GATE, MARKER, checks_pass, current_review,
                    eligible, protected, required_checks, run_result)
from github_api import GitHub

HEAD, NEXT, BASE = "a" * 40, "b" * 40, "c" * 40
REPO = "owner/project"
APP = 987654
RULES = [{"type": "required_status_checks", "parameters": {
    "strict_required_status_checks_policy": True,
    "required_status_checks": [{"context": name, "integration_id": APP if name == GATE else 15368}
                               for name in [GATE, "backend", "frontend", "policy"]]}}]
CONFIG = {"backend.yml": ["backend"], "frontend.yml": ["frontend"]}


def pull():
    return {"number": 1, "commits": 2, "state": "open", "draft": False,
            "created_at": "2026-10-10T10:00:00Z",
            "head": {"sha": HEAD, "ref": "codex/feature", "repo": {"full_name": REPO}},
            "base": {"sha": BASE, "ref": "main", "repo": {"default_branch": "main"}}, "labels": [{"name": "review-first-ci"}]}


def checks():
    return [{"id": i, "name": name, "app": {"id": 15368}, "status": "completed",
             "conclusion": "success"} for i, name in enumerate(["backend", "frontend", "policy"], 1)]


class FakeAPI:
    repo = REPO
    app_id = APP

    def __init__(self):
        self.pull = pull()
        self.data = {}
        self.jobs = {}
        self.started = []
        self.gates = []
        self.notices = []
        self.events = []
        self.duplicate_pr = False
        self.check_results = checks()

    def pr(self, _):
        return copy.deepcopy(self.pull)

    def rules(self, _):
        return copy.deepcopy(RULES)

    def comments(self, _):
        return []

    def state(self, *_):
        return copy.deepcopy(self.data), 5 if self.data else None

    def save(self, number, state, comment_id):
        self.data = copy.deepcopy(state)
        return 5

    def label(self, number, label, add):
        self.pull["labels"] = [l for l in self.pull["labels"] if l["name"] != label]
        if add:
            self.pull["labels"].append({"name": label})

    def gate(self, pr, conclusion, text):
        self.gates.append((pr["head"]["sha"], conclusion, text))

    def checks(self, _):
        return self.check_results

    def statuses(self, _):
        return []

    def request(self, path, method, payload):
        if path.endswith("/dispatches"):
            self.started.append((path.split("/")[2], copy.deepcopy(payload)))
        else:
            self.notices.append(payload)
        return {"id": 9, "created_at": "2026-10-10T10:10:00Z"}

    def runs(self, workflow, head):
        return [copy.deepcopy(r) for r in self.jobs.get(workflow, []) if r["head_sha"] == head]

    def pages(self, path, key=None):
        if path.startswith('commits/'):
            return [self.pr(1)] + ([{**self.pr(1), 'number': 2}] if self.duplicate_pr else [])
        if path.endswith('/events'):
            return self.events
        run_id = int(path.split("/")[2])
        run = next(r for runs in self.jobs.values() for r in runs if r["id"] == run_id)
        return [{"name": name, "conclusion": run.get("job_result", run["conclusion"])}
                for name in CONFIG[run["workflow"]]]

    def finish(self, conclusion="success", job_result=None):
        for workflow, payload in self.started:
            ticket = payload["inputs"]["ticket"]
            if any(r["display_title"] == "review-first-" + ticket for r in self.jobs.get(workflow, [])):
                continue
            run = {"id": sum(map(len, self.jobs.values())) + 20, "workflow": workflow,
                   "display_title": "review-first-" + ticket, "head_sha": payload["inputs"]["expected_base"],
                   "status": "completed", "conclusion": conclusion, "run_attempt": 1}
            if job_result:
                run["job_result"] = job_result
            self.jobs.setdefault(workflow, []).append(run)


class Harness(Controller):
    verdict = "running"

    def review(self, *_):
        return self.verdict


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.controller = Harness(self.api, None, "pilot", CONFIG)

    def initial(self):
        self.controller.reconcile(1)
        self.api.finish()
        self.controller.reconcile(1)
        self.assertTrue(self.api.data["baseline"])

    def test_initial_review_rounds_and_final_gate(self):
        self.initial()
        self.assertEqual(self.api.data["phase"], "review")
        self.assertEqual(len(self.api.started), 2)
        self.api.pull["head"]["sha"] = NEXT
        self.controller.reconcile(1)
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.started), 2, "No test dispatch during review fixes")
        self.controller.verdict = "clean"
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.started), 4)
        self.assertIsNone(self.api.gates[-1][1])
        self.api.finish()
        self.controller.reconcile(1)
        self.assertEqual(self.api.gates[-1][1], "success")

    def test_initial_failure_never_enters_review_only_mode(self):
        self.controller.reconcile(1)
        self.api.finish("failure")
        self.controller.reconcile(1)
        self.assertFalse(self.api.data["baseline"])
        self.assertEqual(self.api.gates[-1][1], "failure")
        self.api.pull["head"]["sha"] = NEXT
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.started), 4)
        self.assertEqual(self.api.data["phase"], "initial")

    def test_only_trusted_base_runs_can_validate_a_ticket(self):
        self.controller.reconcile(1)
        self.assertTrue(all(payload["ref"] == "main" for _, payload in self.api.started))
        self.api.finish()
        for runs in self.api.jobs.values():
            runs[0]["head_sha"] = HEAD  # A PR workflow with identical names/ticket.
        self.assertEqual(self.controller.result(self.api.data), "pending")
        self.controller.reconcile(1)
        self.assertFalse(self.api.data["baseline"])

    def test_skipped_test_jobs_never_validate(self):
        self.controller.reconcile(1)
        self.api.finish(job_result="skipped")
        self.controller.reconcile(1)
        self.assertEqual(self.api.gates[-1][1], "failure")

    def test_new_push_during_final_run_invalidates_result(self):
        self.initial()
        self.controller.verdict = "clean"
        self.controller.reconcile(1)
        self.api.finish()
        self.api.pull["head"]["sha"] = NEXT
        self.controller.verdict = "missing"
        self.controller.reconcile(1)
        self.assertEqual(self.api.data["phase"], "review")
        self.assertIsNone(self.api.gates[-1][1])

    def test_failed_final_reports_once_and_stays_blocked(self):
        self.initial()
        self.controller.verdict = "clean"
        self.controller.reconcile(1)
        self.api.finish("failure")
        self.controller.reconcile(1)
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.notices), 1)
        self.assertEqual(self.api.gates[-1][1], "failure")

    def test_withdrawn_approval_returns_to_review_even_while_workers_run(self):
        for finish_workers in (False, True):
            with self.subTest(finish_workers=finish_workers):
                self.setUp()
                self.initial()
                self.controller.verdict = "clean"
                self.controller.reconcile(1)
                if finish_workers:
                    self.api.finish()
                old_ticket = self.api.data["ticket"]
                self.controller.verdict = "findings"
                self.controller.reviewer = self.api
                self.api.label(1, "awaiting-codex-reping", True)
                self.api.events = [{"id": 21, "event": "labeled",
                                    "label": {"name": "awaiting-codex-reping"}}]
                self.controller.reconcile(1)
                self.assertEqual(self.api.data["phase"], "review")
                self.assertEqual(len(self.api.notices), 1, "The same head can be re-reviewed")
                self.assertIsNone(self.api.gates[-1][1])
                self.controller.verdict = "clean"
                self.controller.reconcile(1)
                self.assertNotEqual(self.api.data["ticket"], old_ticket)
                self.assertEqual(len(self.api.started), 6, "Reapproval requires fresh final validation")

    def test_failed_notification_is_retried_then_recorded_once(self):
        self.api.pull["head"]["ref"] = "claude/feature"
        self.controller.reviewer = self.api
        self.controller.reconcile(1)
        self.api.finish("failure")
        with patch.object(self.api, "request", side_effect=RuntimeError("transport unavailable")):
            with self.assertRaisesRegex(RuntimeError, "transport unavailable"):
                self.controller.reconcile(1)
        self.assertNotIn("failure_reported", self.api.data)
        self.controller.reconcile(1)
        self.assertEqual(self.api.data["failure_reported"], self.api.data["ticket"])
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.notices), 1)
        self.assertTrue(self.api.notices[0]["body"].startswith("@Claude"))

    def test_rollback_dispatches_tests_for_existing_pilot(self):
        self.initial()
        for check in self.api.check_results:
            if check["name"] in {"backend", "frontend"}:
                check["conclusion"] = "skipped"
        self.controller.mode = "classic"
        self.controller.reconcile(1, restore=True)
        self.assertEqual(self.api.data["phase"], "classic")
        self.assertNotIn(ACTIVE, {l["name"] for l in self.api.pull["labels"]})
        self.controller.reconcile(1)
        self.assertIsNone(self.api.gates[-1][1])
        self.api.finish()
        self.controller.reconcile(1)
        self.assertEqual(self.api.gates[-1][1], "success")

    def test_rollback_does_not_restart_unrelated_prs(self):
        self.controller.mode = "classic"
        self.controller.reconcile(1, restore=True)
        self.assertEqual(self.api.started, [])

    def test_rollback_requires_mode_switch_first(self):
        with self.assertRaisesRegex(RuntimeError, "classic"):
            self.controller.reconcile(1, restore=True)

    def test_new_base_invalidates_finished_final_validation(self):
        self.initial()
        self.controller.verdict = "clean"
        self.controller.reconcile(1)
        self.api.finish()
        self.api.pull["base"]["sha"] = "d" * 40
        self.api.pull["mergeable_state"] = "behind"
        self.controller.reconcile(1)
        self.assertIsNone(self.api.gates[-1][1])
        self.assertEqual(len(self.api.started), 4, "Do not spend another full run before the branch is updated")

    def test_identical_commit_in_another_pr_does_not_invalidate_final_validation(self):
        self.initial()
        self.api.duplicate_pr = True
        self.controller.verdict = "clean"
        self.controller.reconcile(1)
        self.api.finish()
        self.controller.reconcile(1)
        self.assertEqual(self.api.gates[-1][1], "success")

    def test_declined_findings_can_be_reviewed_without_an_empty_commit(self):
        self.initial()
        self.controller.reviewer = self.api
        self.controller.verdict = "findings"
        self.api.label(1, "awaiting-codex-reping", True)
        self.api.events = [{"id": 11, "event": "labeled", "label": {"name": "awaiting-codex-reping"}}]
        self.controller.reconcile(1)
        self.assertEqual(self.api.data["requested"]["gate_id"], 11)
        self.assertEqual(len(self.api.notices), 1)
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.notices), 1, "Do not repeat a consumed gate")
        self.api.label(1, "awaiting-codex-reping", True)
        self.api.events.append({"id": 12, "event": "labeled", "label": {"name": "awaiting-codex-reping"}})
        self.controller.reconcile(1)
        self.assertEqual(len(self.api.notices), 2, "An explicit new author gate is a new request")


class PolicyTests(unittest.TestCase):
    def test_no_deferral_without_required_gate_and_strict_base(self):
        self.assertTrue(protected(RULES, APP))
        rules = copy.deepcopy(RULES)
        rules[0]["parameters"]["strict_required_status_checks_policy"] = False
        self.assertFalse(protected(rules, APP))
        rules = copy.deepcopy(RULES)
        rules[0]["parameters"]["required_status_checks"][0]["integration_id"] = None
        self.assertFalse(protected(rules, APP))

    def test_both_authors_and_classic_default(self):
        pr = pull()
        self.assertTrue(eligible(pr, REPO, "pilot"))
        pr["head"]["ref"] = "claude/feature"
        self.assertTrue(eligible(pr, REPO, "pilot"))
        self.assertFalse(eligible(pr, REPO, "classic"))
        self.assertFalse(eligible(pr, REPO, "typo"))
        pr["head"]["repo"]["full_name"] = "fork/project"
        self.assertFalse(eligible(pr, REPO, "enabled"))

    def test_latest_failed_attempt_not_masked_by_old_success(self):
        current = checks()
        current.append({**current[0], "id": 50, "conclusion": "failure"})
        self.assertFalse(checks_pass(current, {("backend", 15368)}))

    def test_required_integration_is_honored_without_assuming_actions(self):
        rules = copy.deepcopy(RULES)
        rules[0]["parameters"]["required_status_checks"] = [
            {"context": "coverage", "integration_id": 42}, {"context": "policy"}]
        required = required_checks(rules)
        current = checks() + [{"id": 20, "name": "coverage", "app": {"id": 42},
                               "status": "completed", "conclusion": "success"}]
        self.assertTrue(checks_pass(current, required))
        current.append({**current[-1], "id": 21, "app": {"id": 15368}, "conclusion": "failure"})
        self.assertTrue(checks_pass(current, required), "Other apps cannot override the required source")
        current[3]["conclusion"] = "failure"
        current[4]["conclusion"] = "success"
        self.assertFalse(checks_pass(current, required), "Same-name Actions success cannot substitute for coverage")

    def test_current_completed_summary_and_fresh_thumbs_up(self):
        pr = pull()
        state = {"opened_head": HEAD}
        summary = {"id": 1, "user": {"login": BOT}, "updated_at": "2026-10-10T10:05:00Z",
                   "body": "<!-- codex-pull-request-review-summary -->\n"
                           f"| 📝 **Code Review** | ✅ **Completed** | `{HEAD[:7]}` | PR opened |"}
        reaction = {"user": {"login": BOT}, "content": "+1", "created_at": "2026-10-10T10:05:01Z"}
        def verdict():
            return current_review(pr, state, [summary], [], [], [reaction])
        self.assertEqual(verdict(), "clean")
        reaction["created_at"] = "2026-10-10T10:04:00Z"
        self.assertEqual(verdict(), "finishing", "Old thumbs-up cannot approve a later summary")
        reaction["created_at"] = "2026-10-10T10:05:01Z"
        summary["body"] = summary["body"].replace(HEAD[:7], NEXT[:7])
        self.assertEqual(verdict(), "missing")

    def test_withdrawn_approval_and_quoted_templates_are_not_clean(self):
        review = {"id": 1, "user": {"login": BOT}, "commit_id": HEAD,
                  "submitted_at": "2026-10-10T10:05:00Z", "state": "DISMISSED",
                  "body": "Codex Review: Didn't find any major issues"}
        self.assertEqual(current_review(pull(), {"opened_head": HEAD}, [], [review], [], []), "findings")

    def test_standalone_approved_verdict_is_terminal(self):
        review = {"id": 1, "user": {"login": BOT}, "commit_id": HEAD,
                  "submitted_at": "2026-10-10T10:05:00Z"}
        for status in ("COMMENTED", "CHANGES_REQUESTED"):
            review.update(state=status, body="Review complete.\nAPPROVED\n")
            self.assertEqual(current_review(pull(), {"opened_head": HEAD}, [], [review], [], []), "clean")
            review["body"] = "Do not accept a quoted `APPROVED` token."
            self.assertEqual(current_review(pull(), {"opened_head": HEAD}, [], [review], [], []), "findings")
        review.update(state="COMMENTED", body="Fix the parser for `Codex Review: Didn't find any major issues`.")
        self.assertEqual(current_review(pull(), {"opened_head": HEAD}, [], [review], [], []), "findings")

    def test_dispatch_admission_rejects_changed_head(self):
        api = FakeAPI()
        api.data = {"ticket": "t", "head": HEAD, "base": BASE, "phase": "final"}
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": NEXT,
               "RFC_PR": "1", "RFC_HEAD": HEAD, "RFC_BASE": BASE, "RFC_TICKET": "t"}
        with patch.dict(os.environ, env), self.assertRaisesRegex(RuntimeError, "stale"):
            admit(api, {}, "pilot")

    def test_dispatch_admission_uses_base_code_and_pr_merge_checkout(self):
        api = FakeAPI()
        api.data = {"ticket": "t", "head": HEAD, "base": BASE, "phase": "final"}
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": BASE,
               "RFC_PR": "1", "RFC_HEAD": HEAD, "RFC_BASE": BASE, "RFC_TICKET": "t"}
        with patch.dict(os.environ, env), patch("main.output") as output:
            admit(api, {}, "pilot")
            output.assert_called_once_with(run=True, full=True, **{"checkout-ref": "refs/pull/1/merge"})
            api.pull["head"]["sha"] = NEXT
            with self.assertRaisesRegex(RuntimeError, "stale"):
                admit(api, {}, "pilot")
        env["GITHUB_SHA"] = HEAD
        api.pull["head"]["sha"] = HEAD
        with patch.dict(os.environ, env), self.assertRaisesRegex(RuntimeError, "stale"):
            admit(api, {}, "pilot")

    def test_base_workflow_completion_reconciles_the_ticket_pr(self):
        event = {"workflow_run": {"head_sha": BASE, "event": "workflow_dispatch",
                 "display_title": "review-first-8-final-" + "a" * 32}}
        with tempfile.NamedTemporaryFile(mode="w") as handle:
            json.dump(event, handle)
            handle.flush()
            env = {"GITHUB_EVENT_PATH": handle.name, "RFC_OPERATION": "reconcile",
                   "RFC_TOKEN": "unused", "RFC_PR": "", "RFC_CONFIG": "{}", "RFC_RESTORE": "false"}
            with patch.dict(os.environ, env), patch("main.GitHub"), patch("main.Controller") as cls:
                main()
                cls.return_value.reconcile.assert_called_once_with(8, False)

    def test_untrusted_review_notice_only_selects_a_pr_to_reconcile(self):
        event = {"workflow_run": {"head_sha": NEXT, "event": "pull_request_review_comment",
                 "display_title": "review-first-event-8", "conclusion": "success"}}
        with tempfile.NamedTemporaryFile(mode="w") as handle:
            json.dump(event, handle)
            handle.flush()
            env = {"GITHUB_EVENT_PATH": handle.name, "RFC_OPERATION": "reconcile",
                   "RFC_TOKEN": "unused", "RFC_PR": "", "RFC_CONFIG": "{}", "RFC_RESTORE": "false"}
            with patch.dict(os.environ, env), patch("main.GitHub"), patch("main.Controller") as cls:
                main()
                cls.return_value.reconcile.assert_called_once_with(8, False)

    def test_checkout_requires_exact_merge_parents(self):
        with patch.dict(os.environ, {"RFC_TICKET": "t", "RFC_HEAD": HEAD, "RFC_BASE": BASE}):
            with patch("subprocess.check_output", return_value=f"{BASE} {HEAD}\n"):
                verify_checkout()
            with patch("subprocess.check_output", return_value=f"{BASE} {NEXT}\n"):
                with self.assertRaisesRegex(RuntimeError, "merge candidate"):
                    verify_checkout()


if __name__ == "__main__":
    unittest.main()
