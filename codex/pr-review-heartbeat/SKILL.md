---
name: pr-review-heartbeat
description: Keep a Codex-owned pull request attached to the Codex desktop task that opened it by scheduling same-chat review polling until approval or a user-owned decision is needed.
---

## Keep Codex-owned PRs attached to their originating task

GitHub review events cannot currently wake an existing Codex desktop task
directly. For a PR that Codex is driving, use a scheduled task inside the
current chat as the supported bridge. An in-chat schedule returns to this exact
task with its existing context; it does not create a replacement author task.

Immediately after opening or adopting a Codex-owned PR:

1. Preserve the repo's owner signal (`codex/*` branch or its equivalent
   `codex-only` label) so Claude's review wake-up remains suppressed.
2. Create a recurring task **inside the current chat**, normally every two
   minutes, named for the repository and PR number. Do not create a standalone
   scheduled task or a new Codex task.
3. Give the scheduled task a durable prompt containing the repository, PR
   number, branch, required checks, and the stop conditions below.

Each scheduled run should:

- Query the PR state, mergeability, current head SHA, required checks,
  submitted Codex reviews, top-level PR conversation comments, and all inline
  review threads. Treat each review or comment database ID as the delivery key
  and keep a handled-event ledger in this task so a later poll cannot process
  the same event twice. Apply the repository's approval rules to top-level
  comments as well as submitted reviews.
- Compare the reviewed commit with the current PR head. If they differ, report
  the review as stale and confirm that each finding still applies before
  changing code.
- If there is no new activity, make no repository or GitHub changes and keep
  the update terse.
- For a new actionable review, resume the author loop in this same task:
  assess every finding, make agreed mechanical fixes, verify them, push, and
  reply to each addressed thread with the fix and commit SHA. Follow the
  repository's own author-side review rules for round caps and scope.
- After a fix head's required checks are green, request the next Codex review
  exactly once for that head, using the repository's bare review trigger. If
  checks fail, diagnose and fix failures caused by the PR before requesting
  another review.
- Treat a merge conflict that prevents checks from starting, a cancelled or
  timed-out required check, and a required status that remains missing as
  recovery states rather than ordinary waiting. Follow the repository's
  mechanical recovery procedure when authorized; otherwise pause and ask the
  user. Do not poll a terminal or permanently blocked CI state forever.
- Stop and ask the user instead of changing code when the author disagrees
  with a finding, a finding needs an architectural or product decision, the
  loop oscillates, or the repository's hard stop is reached.

Pause the scheduled task when Codex gives a repository-recognized final
approval, the PR merges or closes, ownership is handed off, the user asks to
stop, or a disagreement needs the user's decision. Report the terminal reason
in this task. Never merge merely because the review loop approved the PR.

This is a polling bridge, not an event webhook: the desktop app and computer
must remain running, and wake-up latency is bounded by the chosen interval.
Event-driven delivery to an existing desktop task remains deferred until the
product exposes a supported desktop event trigger or task-resume transport.
