---
description: Open a PR with the stack's required discipline — a closing keyword (Closes #N) so the issue closes on merge, a test plan, and the OpenAPI diff when codegen ran.
argument-hint: "[issue number] [short title]"
---

Open a pull request for the current branch following WEDDEsign ceremony.

Gather, then act:

1. **Run** `git status -sb` and `git log --oneline @{u}.. 2>/dev/null || git log --oneline -10` to see what's on the branch, and `git diff --stat $(git merge-base HEAD origin/main 2>/dev/null || echo HEAD~5)..HEAD` for scope.
2. **Ensure the branch is pushed** to `origin` first (`git push -u origin HEAD`).
3. **Body — required structure:**
   - A short summary of *what* and *why*.
   - **A closing keyword on its own line**: `Closes #N` (or `Fixes #N` / `Resolves #N`). This is what closes the issue on merge — a `(#N)` in the title links but does NOT close, which is how boards drift out of sync. If this PR has no issue (pure chore / dep bump / docs typo), add the `no-issue` label instead and say so.
   - **A test plan** — the checks you ran or expect CI to run, as a checklist.
   - **If codegen ran** (the API client / data-contracts regenerated), include the OpenAPI/route diff or call it out explicitly.
4. **Open as a normal (non-draft) PR** so CI + Codex QA trigger immediately. A draft gets **no Codex auto-review** — Codex fires only on a non-draft `opened` / `ready_for_review` event, and the `wake-on-codex-review` loop only starts once Codex posts — so drafting "to be safe" silently strands the entire review loop until someone flips it. To keep review running while signalling a PR shouldn't land yet, open it non-draft with a `do-not-merge` label (and/or a `DO NOT MERGE` title prefix), **not** a draft. That label is a *visible hold for the human merge-gate* (merges need human sign-off, which is what actually blocks the merge), not an automated check — nothing fails CI on it — so don't treat it as a hard gate; a required status check on the label is the way to make it one. Reserve `--draft` for code you genuinely don't want reviewed yet, and then you own running `gh pr ready` the moment it's complete.
5. **Title:** match recent PR style in `git log` (imperative, scoped).
6. After opening, **`subscribe_pr_activity`** on the PR (no need to ask) so Codex review + CI events route in. Handling is the `codex-review-triage` skill.

Do **not** merge — branch protection + human review remain the sign-off.

If arguments were given, use them: `$1` = issue number for the closing keyword, `$ARGUMENTS` = title hint.
