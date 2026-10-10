import copy
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from github_api import GitHub
from main import admit
from policy import checks_pass
from test_review_first import CONFIG, HEAD, REPO, FakeAPI, Harness, checks, pull


class StatusAndTransportTests(unittest.TestCase):
    def test_legacy_status_controls_both_classic_and_final_completion(self):
        for mode in ("classic", "pilot"):
            with self.subTest(mode=mode):
                api = FakeAPI()
                api.check_results = [c for c in checks() if c["name"] != "policy"]
                controller = Harness(api, None, mode, CONFIG)
                statuses = [{"context": "policy", "state": "PENDING", "isRequired": True}]
                with patch.object(api, "statuses", return_value=statuses):
                    if mode == "pilot":
                        controller.reconcile(1)
                        api.finish()
                        controller.verdict = "clean"
                        controller.reconcile(1)
                        api.finish()
                    controller.reconcile(1)
                    self.assertIsNone(api.gates[-1][1])
                    statuses[0]["state"] = "SUCCESS"
                    controller.reconcile(1)
                    self.assertEqual(api.gates[-1][1], "success")

    def test_required_status_contexts_complement_check_runs(self):
        required = {("policy", 15368), ("legacy-ci", None)}
        statuses = [{"context": "legacy-ci", "state": "SUCCESS", "isRequired": True}]
        self.assertTrue(checks_pass(checks(), required, statuses))
        for state in ("PENDING", "ERROR", "FAILURE"):
            statuses[0]["state"] = state
            self.assertFalse(checks_pass(checks(), required, statuses))
        statuses[0]["state"] = "SUCCESS"
        statuses[0]["isRequired"] = False
        self.assertFalse(checks_pass(checks(), required, statuses), "GitHub must identify required evidence")
        self.assertFalse(checks_pass(checks(), required, []), "Absent evidence must not pass")

    def test_same_name_check_and_status_must_both_succeed(self):
        statuses = [{"context": "policy", "state": "PENDING", "isRequired": True}]
        self.assertFalse(checks_pass(checks(), {("policy", None)}, statuses))
        statuses[0]["state"] = "SUCCESS"
        current = checks()
        current[-1]["conclusion"] = "failure"
        self.assertFalse(checks_pass(current, {("policy", None)}, statuses))
        current[-1]["conclusion"] = "success"
        self.assertTrue(checks_pass(current, {("policy", None)}, statuses))

    def test_status_read_is_bound_to_exact_commit_and_pr(self):
        api = GitHub("unused", REPO)
        contexts = [{"context": "legacy-ci", "state": "SUCCESS", "isRequired": True}]
        result = {"data": {"repository": {"object": {"status": {"contexts": contexts}}}}}
        with patch.object(api, "request", return_value=result) as request:
            self.assertEqual(api.statuses(pull()), contexts)
            path, method, payload = request.call_args.args
            self.assertEqual((path, method), ("graphql", "POST"))
            self.assertEqual(payload["variables"], {"owner": "owner", "name": "project", "head": HEAD, "pr": 1})
            self.assertIn("isRequired(pullRequestNumber:$pr)", payload["query"])
        error = copy.deepcopy(result)
        error["errors"] = [{"message": "partial response"}]
        with patch.object(api, "request", return_value=error), self.assertRaises(RuntimeError):
            api.statuses(pull())

    def test_transport_errors_fall_back_to_ordinary_pr_ci(self):
        api = GitHub("not-a-real-token", REPO)
        env = {"GITHUB_EVENT_NAME": "pull_request", "RFC_TICKET": ""}
        for failure in (URLError("DNS failure"), TimeoutError("timeout"), ConnectionResetError("reset")):
            with self.subTest(failure=type(failure).__name__):
                with patch("github_api.urlopen", side_effect=failure), patch.dict(os.environ, env):
                    with patch("main.output") as output:
                        admit(api, {"number": 1}, "pilot")
                        output.assert_called_once_with(run=True, full=False, **{"checkout-ref": ""})
                    with self.assertRaises(RuntimeError) as error:
                        api.request("pulls/1")
                    self.assertNotIn("not-a-real-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
