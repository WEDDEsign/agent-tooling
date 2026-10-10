"""Trusted reconciliation. Never check out or execute pull-request contents."""

import time
import uuid
from datetime import datetime, timezone

from github_api import GitHub
from policy import (ACTIVE, GATE, REPING, checks_pass, codex_author, current_review, eligible,
                    labels, protected, required_checks, run_result, summary_receipt, trusted_base)


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

    def complete(self, pr, message):
        # Required checks certify a commit. Opening another PR at that exact
        # commit does not revoke its validation; PR identity is not a merge gate.
        if self.unchanged(pr):
            self.api.gate(pr, "success", message)

    def review(self, pr, state, comments, checkpoint_id=None):
        n = pr["number"]
        args = (pr, state, comments, self.api.pages(f"pulls/{n}/reviews"),
                self.api.pages(f"pulls/{n}/comments"))
        summaries = [c for c in comments if codex_author(c)
                     and c.get("body", "").startswith("<!-- codex-pull-request-review-summary -->")]
        summary = max(summaries, key=lambda c: c["id"], default=None)

        def reactions():
            found = self.api.pages(f"issues/{n}/reactions")
            if summary:
                found += self.api.pages(f"issues/comments/{summary['id']}/reactions")
            return found

        found = reactions()
        verdict = current_review(*args, found)
        # The summary edit precedes the reaction by a few seconds. Reactions
        # have no Actions event; this short retry avoids an hourly recovery wait.
        if verdict == "finishing":
            time.sleep(10)
            found = reactions()
            verdict = current_review(*args, found)
        if (verdict == "clean" and summary
                and current_review(pr, state, comments, [], args[4], found) == "clean"):
            receipt = summary_receipt(pr, state, summary)
            if state.get("approved_summary") != receipt:
                # A bot reaction signals review completion; it is not a mutable
                # approval switch. Persist the authenticated result. Review
                # changes, findings and summary edits/deletion still invalidate it.
                state["approved_summary"] = receipt
                self.api.save(n, state, checkpoint_id)
        return verdict

    def start(self, pr, state, checkpoint_id, phase):
        if not trusted_base(pr):
            raise RuntimeError("Controlled validation requires the default branch as its target")
        if not self.unchanged(pr):
            return
        state.update(phase=phase, head=pr["head"]["sha"], base=pr["base"]["sha"],
                     ticket=f"{pr['number']}-{phase}-{uuid.uuid4().hex}", dispatched=[])
        # Persist intent before dispatch. A failed dispatch is visible and can
        # be retried by the explicit recovery action; do not blindly duplicate it.
        checkpoint_id = self.api.save(pr["number"], state, checkpoint_id)
        for workflow in self.config:
            self.api.request(f"actions/workflows/{workflow}/dispatches", "POST", {
                "ref": pr["base"]["ref"], "inputs": {
                    "pr_number": str(pr["number"]), "expected_head": state["head"],
                    "expected_base": state["base"], "ticket": state["ticket"]}})
            state["dispatched"].append(workflow)
            self.api.save(pr["number"], state, checkpoint_id)
        return True

    def result(self, state):
        results = []
        for workflow, expected in self.config.items():
            runs = self.api.runs(workflow, state["base"])
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

    def request_review(self, pr, state, checkpoint_id, verdict):
        head = pr["head"]["sha"]
        if REPING not in labels(pr) or verdict in {"clean", "running", "finishing"}:
            return
        activations = [event["id"] for event in self.api.pages(f"issues/{pr['number']}/events")
                       if event.get("event") == "labeled" and event.get("label", {}).get("name") == REPING]
        if not activations:
            raise RuntimeError("Cannot identify the review-gate activation")
        gate_id = max(activations)
        requested = state.get("requested", {})
        if requested.get("head") == head and requested.get("gate_id", 0) >= gate_id:
            return
        rounds = [int(label.removeprefix("codex-round-")) for label in labels(pr)
                  if label.startswith("codex-round-") and label.removeprefix("codex-round-").isdigit()]
        if max(rounds, default=0) >= 6:
            raise RuntimeError("Review round limit reached; owner decision required")
        if not self.reviewer:
            raise RuntimeError("Review transport token is missing")
        if not self.unchanged(pr):
            return
        state["request_intent"] = {"head": head, "gate_id": gate_id,
                                   "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.api.save(pr["number"], state, checkpoint_id)
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
            landed = self.recover_request(pr, state, self.api.comments(pr["number"]), checkpoint_id)
            if not landed:
                self.api.label(pr["number"], REPING, True)
                raise
            return
        state["requested"] = {"head": head, "gate_id": gate_id, "id": comment["id"], "at": comment["created_at"]}
        state.pop("request_intent", None)
        self.api.save(pr["number"], state, checkpoint_id)

    def recover_request(self, pr, state, comments, checkpoint_id, restore_gate=False):
        intent = state.get("request_intent", {})
        if intent.get("head") != pr["head"]["sha"]:
            return False
        landed = [c for c in comments if c.get("body", "").strip() == "@" + "codex review"
                  and c["created_at"] >= intent["at"]
                  and c.get("author_association") in {"OWNER", "MEMBER", "COLLABORATOR"}]
        if not landed:
            if restore_gate and REPING not in labels(pr) and self.unchanged(pr):
                # A previous runner may have stopped after claiming the label.
                # Serialized reconciliation proves no delivery is still active;
                # restore the durable gate rather than stranding that intent.
                self.api.label(pr["number"], REPING, True)
                pr["labels"].append({"name": REPING})
            return False
        comment = min(landed, key=lambda c: c["id"])
        state["requested"] = {"head": intent["head"], "gate_id": intent["gate_id"],
                              "id": comment["id"], "at": comment["created_at"]}
        state.pop("request_intent", None)
        self.api.save(pr["number"], state, checkpoint_id)
        return True

    def failure(self, pr, state, checkpoint_id):
        self.api.gate(pr, "failure", "Validation failed; merge remains blocked")
        if state.get("failure_reported") == state["ticket"]:
            return
        codex_owned = pr["head"]["ref"].startswith("codex/") or "codex-only" in labels(pr)
        message = f"Review-first {state['phase']} validation failed for `{state['head']}`. "
        message += "The PR author should inspect the failed Actions run, fix it, and push. Merge is blocked."
        if not codex_owned and self.reviewer:
            self.reviewer.request(f"issues/{pr['number']}/comments", "POST", {"body": "@Claude — " + message})
        else:
            self.api.request(f"issues/{pr['number']}/comments", "POST", {"body": message})
        state["failure_reported"] = state["ticket"]
        self.api.save(pr["number"], state, checkpoint_id)

    def reconcile(self, number, restore=False):
        pr = self.api.pr(number)
        head, base = pr["head"]["sha"], pr["base"]["sha"]
        if pr["state"] != "open":
            return
        comments = self.api.comments(number)
        state, checkpoint_id = self.api.state(pr, comments)
        rules = self.api.rules(pr)
        required = {check for check in required_checks(rules) if check[0] != GATE}
        workers = {job for jobs in self.config.values() for job in jobs}
        other_required = {check for check in required
                          if check[0] not in workers or check[1] not in {None, -1, 15368}}
        managed = eligible(pr, self.api.repo, self.mode) and protected(rules, self.api.app_id)
        if restore and self.mode != "classic":
            raise RuntimeError("Set CI_REVIEW_MODE=classic before restoring full CI")
        if not managed:
            restored = state.get("phase") == "classic" and state.get("head") == head and state.get("base") == base
            deferred = (ACTIVE in labels(pr) or (state.get("head") == head
                        and state.get("phase") in {"initial", "review", "final", "waiting"}))
            leaving_pilot = (deferred and not restored and trusted_base(pr)
                             and (pr["head"].get("repo") or {}).get("full_name") == self.api.repo)
            if (restore and (state or ACTIVE in labels(pr))) or leaving_pilot:
                state.update(version=2, baseline=False)
                self.api.gate(pr, None, "Restoring full CI for this head")
                if self.start(pr, state, checkpoint_id, "classic") and ACTIVE in labels(pr):
                    self.api.label(number, ACTIVE, False)
                return
            if ACTIVE in labels(pr):
                self.api.label(number, ACTIVE, False)
            success = checks_pass(self.api.checks(head), other_required if restored else required,
                                  self.api.statuses(pr))
            if restored:
                success = success and self.result(state) == "success"
            if self.unchanged(pr):
                if success:
                    self.complete(pr, "Classic CI passed")
                else:
                    self.api.gate(pr, None, "Waiting for normal required checks")
            return
        if not self.config or not all(isinstance(v, list) and v for v in self.config.values()):
            raise RuntimeError("Validation workflow/job configuration is empty or malformed")
        if ACTIVE not in labels(pr):
            self.api.label(number, ACTIVE, True)
        moved = state and (state.get("head") != head or state.get("base") != base)
        if moved:
            # Carry the baseline before creating a current-head gate, including
            # when GitHub has not computed mergeability yet. An empty gate is
            # authoritative and would otherwise hide the prior checkpoint.
            state.update(head=head, base=base, phase="review" if state.get("baseline") else "waiting",
                         ticket="")
            checkpoint_id = self.api.save(number, state, checkpoint_id)
        self.api.gate(pr, None, "Waiting for review and final validation")
        if pr.get("mergeable_state") in {"dirty", "unknown", "behind"}:
            self.api.gate(pr, None, "Waiting for an up-to-date, mergeable branch")
            return
        if not state or state.get("phase") in {"classic", "waiting"}:
            state = {"version": 2, "baseline": False, "opened_head": head}
            self.start(pr, state, checkpoint_id, "initial")
            return
        if state["phase"] == "final" and self.review(pr, state, comments, checkpoint_id) != "clean":
            # Approval can be withdrawn without a push. An author may resolve
            # or decline findings and re-arm the gate for this same head.
            state.update(phase="review", ticket="")
            checkpoint_id = self.api.save(number, state, checkpoint_id)
        if state["phase"] in {"initial", "final"}:
            result = self.result(state)
            if result == "failure":
                self.failure(pr, state, checkpoint_id)
                return
            if result != "success":
                return
            if state["phase"] == "initial":
                state.update(baseline=True, phase="review")
                checkpoint_id = self.api.save(number, state, checkpoint_id)
            elif (self.review(pr, state, comments, checkpoint_id) == "clean"
                  and checks_pass(self.api.checks(head), other_required, self.api.statuses(pr))
                  and self.unchanged(pr)):
                self.complete(pr, "Current review and final validation passed")
                return
        if state["phase"] == "review":
            self.recover_request(pr, state, comments, checkpoint_id, restore_gate=True)
            verdict = self.review(pr, state, comments, checkpoint_id)
            if verdict == "clean":
                self.start(pr, state, checkpoint_id, "final")
            else:
                self.request_review(pr, state, checkpoint_id, verdict)
