"""Small GitHub adapter; secrets stay in authorization headers, never output."""

import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from policy import MARKER


class GitHub:
    def __init__(self, token, repo=None):
        self.token = token
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]

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

    def state(self, comments):
        found = [c for c in comments if c["user"]["login"] == "github-actions[bot]"
                 and (c.get("performed_via_github_app") or {}).get("id") == 15368
                 and c.get("body", "").startswith(MARKER)]
        if not found:
            return {}, None
        latest = max(found, key=lambda c: c["id"])
        state = json.loads(latest["body"].split("```json\n", 1)[1].split("\n```", 1)[0])
        if state.get("version") != 1:
            raise RuntimeError("Unknown review-first state version")
        return state, latest["id"]

    def save(self, number, state, comment_id):
        body = (MARKER + "\nReview-first CI: **" + state["phase"] + "**. "
                "The required merge-validation check decides whether validation is complete.\n\n"
                "<details><summary>Controller state</summary>\n\n```json\n"
                + json.dumps(state, sort_keys=True) + "\n```\n</details>")
        path = f"issues/comments/{comment_id}" if comment_id else f"issues/{number}/comments"
        response = self.request(path, "PATCH" if comment_id else "POST", {"body": body})
        return response["id"]

    def label(self, number, name, add):
        if add:
            self.request(f"issues/{number}/labels", "POST", {"labels": [name]})
        else:
            self.request(f"issues/{number}/labels/{quote(name, safe='')}", "DELETE")

    def gate(self, head, conclusion, text):
        existing = [c for c in self.checks(head) if c["name"] == "merge-validation"
                    and c.get("app", {}).get("id") == 15368]
        payload = {"name": "merge-validation", "status": "completed" if conclusion else "in_progress",
                   "output": {"title": text, "summary": text}}
        if conclusion:
            payload["conclusion"] = conclusion
        if existing:
            check = max(existing, key=lambda c: c["id"])
            self.request(f"check-runs/{check['id']}", "PATCH", payload)
        else:
            self.request("check-runs", "POST", {**payload, "head_sha": head})

    def runs(self, workflow, head):
        return self.pages(f"actions/workflows/{quote(workflow, safe='')}/runs?"
                          + urlencode({"event": "workflow_dispatch", "head_sha": head}), "workflow_runs")
