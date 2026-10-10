"""Trusted reconciliation. Never check out or execute pull-request contents."""

import time
import uuid
from datetime import datetime, timezone

from github_api import GitHub
from policy import (ACTIVE, GATE, REPING, checks_pass, current_review, eligible,
                    labels, protected, required_checks, run_result)


class Controller:
    def __init__(self, api, review_token, mode, config):
        self.api = api
        self.reviewer = GitHub(review_token, api.repo) if review_token else None
        self.mode = mode or "classic"
        self.config = config

    def unchanged(self, pr):
        now = self.api.pr(pr["number"])
        return (now["state"] == "open" and now["head"]["sha"] == pr["head"]["sha"]
                and now["base"]["sha"] == pr["base"]["sha"] and not now.get("draft"))

    def review(self, pr, state, comments):
        n = pr["number"]
        args = (pr, state, comments, self.api.pages(f"pulls/{n}/reviews"),
                self.api.pages(f"pulls/{n}/comments"))
        verdict = current_review(*args, self.api.pages(f"issues/{n}/reactions"))
        # The summary edit precedes the reaction by a few seconds. Reactions
        # have no Actions event; this short retry avoids an hourly recovery wait.
        if verdict == "finishing":
            time.sleep(10)
            verdict = current_review(*args, self.api.pages(f"issues/{n}/reactions"))
        return verdict

    def start(self, pr, state, comment_id, phase):
        if not self.unchanged(pr):
            return
        state.update(phase=phase, head=pr["head"]["sha"], base=pr["base"]["sha"],
                     ticket=f"{pr['number']}-{phase}-{uuid.uuid4().hex}", dispatched=[])
        # Persist intent before dispatch. A failed dispatch is visible and can
        # be retried by the explicit recovery action; do not blindly duplicate it.
        comment_id = self.api.save(pr["number"], state, comment_id)
        for workflow in self.config:
            self.api.request(f"actions/workflows/{workflow}/dispatches", "POST", {
                "ref": pr["head"]["ref"], "inputs": {
                    "pr_number": str(pr["number"]), "expected_head": state["head"],
                    "expected_base": state["base"], "ticket": state["ticket"]}})
            state["dispatched"].append(workflow)
            self.api.save(pr["number"], state, comment_id)

    def result(self, state):
        results = []
        for workflow, expected in self.config.items():
            runs = self.api.runs(workflow, state["head"])
            matching = [r for r in runs if r.get("display_title") == "review-first-" + state["ticket"]]
            jobs = []
            if matching:
                latest = max(matching, key=lambda r: (r["id"], r.get("run_attempt", 1)))
                latest["expected_jobs"] = expected
                # The attempts endpoint excludes jobs left over from an older retry.
                jobs = self.api.pages(f"actions/runs/{latest['id']}/attempts/"
                                      f"{latest.get('run_attempt', 1)}/jobs", "jobs")
            results.append(run_result(matching, state["ticket"], jobs))
        if "failure" in results:
            return "failure"
        return "success" if results and all(r == "success" for r in results) else "pending"

    def request_review(self, pr, state, comment_id, verdict):
        head = pr["head"]["sha"]
        if (REPING not in labels(pr) or state.get("requested", {}).get("head") == head
                or verdict in {"clean", "running", "finishing", "findings"}):
            return
        rounds = [int(label.removeprefix("codex-round-")) for label in labels(pr)
                  if label.startswith("codex-round-") and label.removeprefix("codex-round-").isdigit()]
        if max(rounds, default=0) >= 6:
            raise RuntimeError("Review round limit reached; owner decision required")
        if not self.reviewer:
            raise RuntimeError("Review transport token is missing")
        if not self.unchanged(pr):
            return
        state["request_intent"] = {"head": head, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.api.save(pr["number"], state, comment_id)
        # Same atomic label claim as the legacy transport. Legacy consumers
        # exclude ACTIVE, so only this controller may claim a pilot round.
        self.api.label(pr["number"], REPING, False)
        if not self.unchanged(pr):
            self.api.label(pr["number"], REPING, True)
            return
        try:
            comment = self.reviewer.request(f"issues/{pr['number']}/comments", "POST",
                                            {"body": "@" + "codex review"})
        except RuntimeError:
            landed = self.recover_request(pr, state, self.api.comments(pr["number"]), comment_id)
            if not landed:
                self.api.label(pr["number"], REPING, True)
                raise
            return
        state["requested"] = {"head": head, "id": comment["id"], "at": comment["created_at"]}
        self.api.save(pr["number"], state, comment_id)

    def recover_request(self, pr, state, comments, comment_id):
        intent = state.get("request_intent", {})
        if intent.get("head") != pr["head"]["sha"]:
            return False
        landed = [c for c in comments if c.get("body", "").strip() == "@" + "codex review"
                  and c["created_at"] >= intent["at"]
                  and c.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}]
        if not landed:
            return False
        comment = min(landed, key=lambda c: c["id"])
        state["requested"] = {"head": intent["head"], "id": comment["id"], "at": comment["created_at"]}
        state.pop("request_intent", None)
        self.api.save(pr["number"], state, comment_id)
        return True

    def failure(self, pr, state, comment_id):
        self.api.gate(state["head"], "failure", "Validation failed; merge remains blocked")
        if state.get("failure_reported") == state["ticket"]:
            return
        state["failure_reported"] = state["ticket"]
        self.api.save(pr["number"], state, comment_id)
        codex_owned = pr["head"]["ref"].startswith("codex/") or "codex-only" in labels(pr)
        message = f"Review-first {state['phase']} validation failed for `{state['head']}`. "
        message += "The PR author should inspect the failed Actions run, fix it, and push. Merge is blocked."
        if not codex_owned and self.reviewer:
            self.reviewer.request(f"issues/{pr['number']}/comments", "POST", {"body": "@Claude — " + message})
        else:
            self.api.request(f"issues/{pr['number']}/comments", "POST", {"body": message})

    def reconcile(self, number, restore=False):
        pr = self.api.pr(number)
        head, base = pr["head"]["sha"], pr["base"]["sha"]
        if pr["state"] != "open":
            return
        comments = self.api.comments(number)
        state, comment_id = self.api.state(comments)
        rules = self.api.rules(pr)
        required = required_checks(rules) - {GATE}
        managed = eligible(pr, self.api.repo, self.mode) and protected(rules)
        if restore and self.mode != "classic":
            raise RuntimeError("Set CI_REVIEW_MODE=classic before restoring full CI")
        if not managed:
            if ACTIVE in labels(pr):
                self.api.label(number, ACTIVE, False)
            if restore and (state or ACTIVE in labels(pr)):
                state.update(version=1, baseline=False)
                self.api.gate(head, None, "Restoring full CI for this head")
                self.start(pr, state, comment_id, "classic")
                return
            restored = state.get("phase") == "classic" and state.get("head") == head and state.get("base") == base
            success = checks_pass(self.api.checks(head), required)
            if restored:
                success = success and self.result(state) == "success"
            if self.unchanged(pr):
                self.api.gate(head, "success" if success else None,
                              "Classic CI passed" if success else "Waiting for normal required checks")
            return
        if not self.config or not all(isinstance(v, list) and v for v in self.config.values()):
            raise RuntimeError("Validation workflow/job configuration is empty or malformed")
        if ACTIVE not in labels(pr):
            self.api.label(number, ACTIVE, True)
        self.api.gate(head, None, "Waiting for review and final validation")
        if not state or state.get("phase") == "classic":
            state = {"version": 1, "baseline": False, "opened_head": head}
            self.start(pr, state, comment_id, "initial")
            return
        moved = state.get("head") != head or state.get("base") != base
        if moved:
            if not state.get("baseline"):
                self.start(pr, state, comment_id, "initial")
                return
            state.update(head=head, base=base, phase="review", ticket="")
            comment_id = self.api.save(number, state, comment_id)
        if state["phase"] in {"initial", "final"}:
            result = self.result(state)
            if result == "failure":
                self.failure(pr, state, comment_id)
                return
            if result != "success":
                return
            if state["phase"] == "initial":
                state.update(baseline=True, phase="review")
                comment_id = self.api.save(number, state, comment_id)
            elif (self.review(pr, state, comments) == "clean"
                  and checks_pass(self.api.checks(head), required - {
                      job for jobs in self.config.values() for job in jobs}) and self.unchanged(pr)):
                self.api.gate(head, "success", "Current review and final validation passed")
                return
        if state["phase"] == "review":
            self.recover_request(pr, state, comments, comment_id)
            verdict = self.review(pr, state, comments)
            if verdict == "clean":
                self.start(pr, state, comment_id, "final")
            else:
                self.request_review(pr, state, comment_id, verdict)
