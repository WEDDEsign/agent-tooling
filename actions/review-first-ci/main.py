"""Composite action entrypoint. Admission is read-only; reconciliation is trusted."""

import json
import os
import re
import subprocess

from controller import Controller
from github_api import GitHub
from policy import eligible, protected


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        for key, value in values.items():
            handle.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def admit(api, event, mode):
    name = os.environ["GITHUB_EVENT_NAME"]
    ticket = os.environ.get("RFC_TICKET", "")
    if name == "workflow_dispatch" and ticket:
        number = int(os.environ["RFC_PR"])
        head, base = os.environ["RFC_HEAD"], os.environ["RFC_BASE"]
        if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (head, base)):
            raise RuntimeError("Validation requires full immutable commit IDs")
        pr = api.pr(number)
        state, _ = api.state(api.comments(number))
        if (pr["state"] != "open" or pr["head"]["sha"] != head or pr["base"]["sha"] != base
                or os.environ["GITHUB_SHA"] != base or state.get("ticket") != ticket
                or state.get("head") != head or state.get("base") != base
                or state.get("phase") not in {"initial", "final", "classic"}):
            raise RuntimeError("Validation request is stale or does not belong to this controller")
        output(run=True, full=True, **{"checkout-ref": f"refs/pull/{number}/merge"})
        return
    # Main, ordinary manual runs and a disabled feature preserve existing CI.
    if name != "pull_request" or mode not in {"pilot", "enabled"}:
        output(run=True, full=False, **{"checkout-ref": ""})
        return
    try:
        pr = api.pr(event["number"])
        defer = eligible(pr, api.repo, mode) and protected(api.rules(pr))
    except RuntimeError as error:
        print(f"Cannot establish pilot prerequisites; running normal CI: {error}")
        defer = False
    output(run=not defer, full=False, **{"checkout-ref": ""})


def verify_checkout():
    if not os.environ.get("RFC_TICKET"):
        return
    parents = subprocess.check_output(["git", "show", "-s", "--format=%P", "HEAD"], text=True).split()
    if parents != [os.environ["RFC_BASE"], os.environ["RFC_HEAD"]]:
        raise RuntimeError("Checkout is not the requested base/head merge candidate")


def main():
    api = GitHub(os.environ["RFC_TOKEN"])
    with open(os.environ["GITHUB_EVENT_PATH"]) as handle:
        event = json.load(handle)
    operation = os.environ["RFC_OPERATION"]
    mode = os.environ.get("RFC_MODE") or "classic"
    if operation == "admit":
        admit(api, event, mode)
    elif operation == "verify-checkout":
        verify_checkout()
    elif operation == "reconcile":
        config = json.loads(os.environ.get("RFC_CONFIG", "{}"))
        controller = Controller(api, os.environ.get("RFC_REVIEW_TOKEN"), mode, config)
        explicit = os.environ.get("RFC_PR")
        if explicit:
            numbers = [int(explicit)]
        elif "pull_request" in event:
            numbers = [event["pull_request"]["number"]]
        elif event.get("issue", {}).get("pull_request"):
            numbers = [event["issue"]["number"]]
        elif "workflow_run" in event:
            run = event["workflow_run"]
            ticket = re.fullmatch(r"review-first-(\d+)-(?:initial|final|classic)-[0-9a-f]{32}",
                                  run.get("display_title", ""))
            if ticket and run.get("event") == "workflow_dispatch":
                # Dispatched workflow code belongs to base; its commit is not
                # the PR head. The ticket selects which live state to reconcile.
                numbers = [int(ticket[1])]
            else:
                prs = api.pages(f"commits/{run['head_sha']}/pulls")
                numbers = [p["number"] for p in prs if p["state"] == "open"]
        else:
            numbers = [p["number"] for p in api.pages("pulls?state=open")]
        failures = []
        for number in numbers:
            try:
                controller.reconcile(number, os.environ.get("RFC_RESTORE") == "true")
            except (RuntimeError, KeyError, ValueError) as error:
                failures.append(f"PR #{number}: {error}")
        if failures:
            raise RuntimeError("; ".join(failures))
    else:
        raise RuntimeError("Unknown operation")


if __name__ == "__main__":
    main()
