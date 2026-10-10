"""Small GitHub adapter; secrets stay in authorization headers, never output."""

import json
import os
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from policy import GATE, MARKER, dedicated_app


class GitHub:
    def __init__(self, token, repo=None, app_id=0):
        self.token = token
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]
        self.app_id = int(app_id or 0)

    def request(self, path, method="GET", data=None):
        raw = None if data is None else json.dumps(data).encode()
        url = ("https://api.github.com/graphql" if path == "graphql" else
               "https://api.github.com/repos/" + self.repo + "/" + path)
        request = Request(url,
                          data=raw, method=method, headers={
                              "Authorization": "Bearer " + self.token,
                              "Accept": "application/vnd.github+json",
                              "X-GitHub-Api-Version": "2022-11-28",
                              "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as error:
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]}: HTTP {error.code}") from None
        except (URLError, OSError, HTTPException, json.JSONDecodeError) as error:
            # Keep fallback/retry behavior consistent even without an HTTP
            # response. Exception reasons can contain request details.
            raise RuntimeError(f"GitHub {method} {path.split('?')[0]}: {type(error).__name__}") from None

    def pages(self, path, key=None):
        result = []
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            value = self.request(f"{path}{separator}per_page=100&page={page}")
            items = value[key] if key else value
            result.extend(items)
            if len(items) < 100:
                return result
        raise RuntimeError("GitHub pagination limit reached; refusing partial evidence")

    def pr(self, number):
        return self.request(f"pulls/{int(number)}")

    def rules(self, pr):
        return self.pages("rules/branches/" + quote(pr["base"]["ref"], safe=""))

    def checks(self, head):
        return self.pages(f"commits/{head}/check-runs", "check_runs")

    def statuses(self, pr):
        # Status.contexts is GitHub's current set (not the REST status history).
        # isRequired lets GitHub apply the PR's source binding; REST statuses
        # expose a creator but no integration ID, so do not guess one from login.
        # https://docs.github.com/en/graphql/reference/commits#statuscontext
        owner, name = self.repo.split("/", 1)
        response = self.request("graphql", "POST", {
            "query": """query($owner:String!,$name:String!,$head:GitObjectID!,$pr:Int!){
              repository(owner:$owner,name:$name){object(oid:$head){... on Commit{
                status{contexts{context state isRequired(pullRequestNumber:$pr)}}
              }}}
            }""",
            "variables": {"owner": owner, "name": name, "head": pr["head"]["sha"], "pr": pr["number"]}})
        if response.get("errors"):
            raise RuntimeError("GitHub could not evaluate required commit statuses")
        commit = response["data"]["repository"]["object"]
        if commit is None:
            raise RuntimeError("GitHub could not find the requested commit")
        return (commit.get("status") or {}).get("contexts", [])

    def comments(self, number):
        return self.pages(f"issues/{number}/comments")

    def owned(self, check, number):
        return (dedicated_app(self.app_id) and check.get("app", {}).get("id") == self.app_id
                and check.get("name") == GATE
                and check.get("external_id") == f"review-first:{self.repo}:{number}")

    def checkpoint(self, head, number):
        return max((c for c in self.checks(head) if self.owned(c, number)),
                   key=lambda c: c["id"], default=None)

    def decode_state(self, check, number):
        if not self.owned(check, number) or not check.get("output", {}).get("text"):
            return {}
        state = json.loads(check["output"]["text"])
        if state.get("version") != 2 or state.get("head") != check["head_sha"]:
            raise RuntimeError("Invalid App-owned controller state")
        return state

    def state(self, pr, comments):
        current = self.checkpoint(pr["head"]["sha"], pr["number"])
        if current:
            # Always prefer the authoritative current-head record, including an
            # empty one. A copied old pointer cannot roll its state back.
            return self.decode_state(current, pr["number"]), current["id"]
        # Comments only locate previous-head checkpoints across pushes. They
        # contain no trusted state. Read each referenced check from GitHub and
        # authenticate its App AND repository/PR binding before using it.
        ids = set()
        for comment in comments:
            match = re.match(re.escape(MARKER) + r"\ncheck: (\d+)\n", comment.get("body", ""))
            if match:
                ids.add(int(match[1]))
        for check_id in sorted(ids, reverse=True):
            check = self.request(f"check-runs/{check_id}")
            state = self.decode_state(check, pr["number"])
            if state:
                return state, None  # Save a new head's checkpoint, never edit the old one.
        return {}, None

    def save(self, number, state, checkpoint_id):
        check_id = self.write_check(number, state["head"], None,
                                   "Review-first CI: " + state["phase"], state)
        if checkpoint_id != check_id:
            self.request(f"issues/{number}/comments", "POST", {"body":
                f"{MARKER}\ncheck: {check_id}\n"
                f"Controller state for `{state['head']}` lives in the dedicated App's "
                f"[merge-validation check](https://github.com/{self.repo}/runs/{check_id}). "
                "This comment is only a pointer; its contents cannot certify validation."})
        return check_id

    def label(self, number, name, add):
        if add:
            self.request(f"issues/{number}/labels", "POST", {"labels": [name]})
        else:
            self.request(f"issues/{number}/labels/{quote(name, safe='')}", "DELETE")

    def gate(self, pr, conclusion, text):
        self.write_check(pr["number"], pr["head"]["sha"], conclusion, text)

    def write_check(self, number, head, conclusion, text, state=None):
        if not dedicated_app(self.app_id):
            raise RuntimeError("A dedicated controller App ID is required")
        existing = self.checkpoint(head, number)
        if state is not None and existing and existing.get("status") == "completed":
            # Recording notification delivery must not turn a failed gate back
            # into a pending one. Only gate() changes the validation verdict.
            conclusion = existing.get("conclusion")
        payload = {"name": GATE, "status": "completed" if conclusion else "in_progress",
                   "external_id": f"review-first:{self.repo}:{number}",
                   "output": {"title": text, "summary": text}}
        if state is not None:
            payload["output"]["text"] = json.dumps(state, sort_keys=True)
        elif existing and existing.get("output", {}).get("text"):
            payload["output"]["text"] = existing["output"]["text"]
        if conclusion:
            payload["conclusion"] = conclusion
        if existing:
            response = self.request(f"check-runs/{existing['id']}", "PATCH", payload)
        else:
            response = self.request("check-runs", "POST", {**payload, "head_sha": head})
        if not self.owned(response, number):
            raise RuntimeError("GitHub did not write a check owned by the configured App")
        return response["id"]

    def runs(self, workflow, head):
        return self.pages(f"actions/workflows/{quote(workflow, safe='')}/runs?"
                          + urlencode({"event": "workflow_dispatch", "head_sha": head}), "workflow_runs")
