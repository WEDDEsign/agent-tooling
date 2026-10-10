"""Pure decisions shared by admission and the trusted metadata controller."""

import hashlib
import re
from datetime import datetime, timedelta, timezone

BOT = "chatgpt-codex-connector[bot]"
GATE = "merge-validation"
STATE = "review-first-state"
ACTIVE = "review-first-ci-active"
OPT_IN = "review-first-ci"
REPING = "awaiting-codex-reping"
MARKER = "<!-- review-first-ci:v2 -->"


def codex_author(item):
    return (item.get("user") or {}).get("login") == BOT


def summary_receipt(pr, state, summary):
    return {"head": pr["head"]["sha"], "id": summary["id"],
            "updated_at": summary.get("updated_at"),
            "body_sha256": hashlib.sha256(summary["body"].encode("utf-8")).hexdigest(),
            "activation": state.get("requested", {})}


def dedicated_app(app_id):
    return isinstance(app_id, int) and app_id > 0 and app_id != 15368


def recent_completion(value):
    try:
        completed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - completed
    except (TypeError, ValueError):
        return False
    return timedelta(0) <= age < timedelta(days=7)


def trusted_base(pr):
    return pr["base"]["ref"] == pr["base"].get("repo", {}).get("default_branch")


def labels(pr):
    return {label["name"] for label in pr.get("labels", [])}


def eligible(pr, repo, mode):
    if pr["state"] != "open" or pr.get("draft") or not trusted_base(pr):
        return False
    if (pr["head"].get("repo") or {}).get("full_name") != repo or "ci-always" in labels(pr):
        return False
    if mode == "pilot":
        return OPT_IN in labels(pr)
    if mode == "enabled":
        return (OPT_IN in labels(pr) or "codex-only" in labels(pr)
                or pr["head"]["ref"].startswith(("codex/", "claude/")))
    return False


def required_checks(rules):
    return {(check["context"], check.get("integration_id")) for rule in rules
            if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]}


def protected(rules, app_id):
    """Never defer until GitHub itself enforces the gate and current base."""
    return dedicated_app(app_id) and any(rule["type"] == "required_status_checks"
               and rule["parameters"].get("strict_required_status_checks_policy")
               and any(c["context"] == GATE and c.get("integration_id") == app_id
                       for c in rule["parameters"]["required_status_checks"])
               for rule in rules)


def checks_pass(checks, required, statuses=()):
    for name, source in required:
        candidates = [check for check in checks if check["name"] == name
                      and (source in {None, -1} or check.get("app", {}).get("id") == source)]
        latest = max(candidates, key=lambda c: c["id"], default={})
        contexts = [s for s in statuses if s["context"] == name and s["isRequired"]]
        if not latest and not contexts:
            return False
        # If a required name exists as both a check and a commit status,
        # GitHub requires both. Success in one must not hide failure in the other.
        # Match GitHub's native required-check semantics. Controlled validation
        # workers have a separate, success-only contract in run_result().
        if latest and (latest.get("status") != "completed"
                       or latest.get("conclusion") not in {"success", "neutral", "skipped"}):
            return False
        if any(s["state"] != "SUCCESS" for s in contexts):
            return False
    return True


def current_review(pr, state, comments, reviews, inline, reactions):
    """A clean result must identify this head and its activation, not just be new."""
    head = pr["head"]["sha"]
    requested = state.get("requested", {})
    if requested.get("head") == head:
        since = requested["at"]
    elif state.get("opened_head") == head:
        since = pr["created_at"]
    else:
        return "missing"
    # An edited old inline finding is new feedback even if its thread remains
    # resolved. A new review activation may supersede it after author triage.
    findings = [c for c in inline if codex_author(c)
                and (c.get("commit_id") == head or c.get("updated_at", "") > c.get("created_at", ""))
                and max(c.get("created_at", ""), c.get("updated_at", "")) >= since]
    current = [r for r in reviews if codex_author(r)
               and r.get("commit_id") == head and r.get("submitted_at", "") >= since]
    if current:
        latest = max(current, key=lambda r: r["id"])
        if any(max(c.get("created_at", ""), c.get("updated_at", "")) >= latest["submitted_at"]
               for c in findings):
            return "findings"
        if latest["state"] == "DISMISSED":
            return "findings"
        # The established terminal verdict is a standalone line, not a quoted
        # token in a finding. It also appears in COMMENTED/CHANGES_REQUESTED.
        if re.search(r"(?m)^APPROVED\s*$", latest.get("body", "")):
            return "clean"
        if latest["state"] == "CHANGES_REQUESTED":
            return "findings"
        if latest["state"] == "APPROVED" or latest.get("body", "").lstrip().lower().startswith(
                "codex review: didn't find any major issues"):
            return "clean"
    summaries = [c for c in comments if codex_author(c)
                 and c.get("body", "").startswith("<!-- codex-pull-request-review-summary -->")
                 and c.get("updated_at", "") >= since]
    if not summaries:
        return "findings" if current else "missing"
    summary = max(summaries, key=lambda c: c["id"])
    body = summary["body"]
    rows = [line for line in body.splitlines() if "**Code Review**" in line]
    if len(rows) != 1:
        return "missing"
    row = rows[0]
    commits = re.findall(r"`([0-9a-f]{7,40})`", row)
    if len(commits) != 1 or not head.startswith(commits[0]):
        return "missing"
    if "**Running**" in row:
        return "running"
    if "**Completed**" not in row:
        return "missing"
    thumbs_up = any(codex_author(r) and r["content"] == "+1"
                    and r.get("created_at", "") >= max(since, summary["updated_at"])
                    for r in reactions)
    recorded = state.get("approved_summary") == summary_receipt(pr, state, summary)
    return "clean" if (thumbs_up or recorded) and not findings else "findings" if findings else "finishing"


def run_result(runs, ticket, jobs):
    matching = [r for r in runs if r.get("display_title") == "review-first-" + ticket]
    if not matching:
        return "pending"
    latest = max(matching, key=lambda r: (r["id"], r.get("run_attempt", 1)))
    if latest["status"] != "completed":
        return "pending"
    if latest["conclusion"] != "success":
        return "failure"
    actual = {j["name"]: j for j in jobs}
    # An all-skipped workflow is green in Actions, but is not validation.
    if not all(actual.get(name, {}).get("conclusion") == "success" for name in latest["expected_jobs"]):
        return "failure"
    return "success" if all(recent_completion(actual[name].get("completed_at"))
                            for name in latest["expected_jobs"]) else "expired"
