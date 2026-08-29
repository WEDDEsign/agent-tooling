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

1. Ensure the repo's owner signal exists so Claude's review wake-up remains
   suppressed. A `codex/*` branch supplies it; for every other branch, add the
   equivalent `codex-only` label before the first review. Adoption must
   establish a missing signal, not merely preserve one that may not exist.
2. Create a recurring task **inside the current chat**, normally every two
   minutes, named for the repository and PR number. Do not create a standalone
   scheduled task or a new Codex task.
3. Give the scheduled task a durable prompt containing the repository, PR
   number, branch, required checks, and the stop conditions below.
4. Record a head-activation boundary in this task. For an initially ready PR,
   use the PR creation event. A draft has no active review boundary: when it
   becomes ready, use its `ready_for_review` event. For every later head, use
   the database ID and creation time of the bare review-trigger comment posted
   for that exact head.
   When adopting an existing head that has neither boundary, first read and
   classify its existing reviews, comments, and unresolved threads, process any
   actionable feedback, and only then ledger those events. Record the adoption
   check's time as that head's fallback boundary. This boundary makes a later
   comment-based approval attributable to the current head and starts the
   no-verdict timer; never accept a pre-adoption approval signal against it.

Each scheduled run should:

- Treat a draft PR as an inactive review loop. Poll only enough state to notice
  that it became ready; do not run the no-verdict timer, advance a round, arm a
  gate, or request review while it is draft. On the ready transition, record
  that event as the initial activation boundary and resume the normal loop.
- Query the PR state, mergeability, current head SHA, required checks, all
  submitted reviews (including `lastEditedAt`), top-level PR conversation
  comments (including author, association, and `updatedAt`), and all inline
  review threads and comments (including each comment's `updatedAt`). Also
  query the PR timeline's `ReviewRequestedEvent` and
  `ReviewRequestRemovedEvent` history. Apply those events chronologically to
  preserve requests that disappeared because the reviewer submitted while
  expiring identities that were explicitly unrequested. Use each review's
  `(database ID, lastEditedAt)` pair and each mutable comment's `(database ID,
  updatedAt)` pair as its delivery key. Keep a handled-event ledger and the
  resulting requested-reviewer state in this task so an unchanged event cannot
  be processed twice while an edited event is re-evaluated. Also keep the
  last-seen resolution state for every thread: a
  resolved-to-unresolved transition is new activity even when the thread
  contains no new comment ID.
- Apply the repository's approval rules only after authenticating the signal.
  A submitted review supplies its reviewer identity, but a token-bearing
  submitted review still requires authorization: reject the PR author and
  require either an explicitly requested reviewer or a non-author repository
  role the repository recognizes as a reviewer (for example `OWNER`, `MEMBER`,
  or `COLLABORATOR`). Apply the same authorization to a top-level approval
  token. For every repository-recognized Codex approval form, accept the
  configured Codex review bot identity without requiring a review request or
  repository role, and reject that Codex-specific form from any other author.
- Compare the reviewed commit with the current PR head. If they differ, report
  the review as stale and confirm that each finding still applies before
  changing code. Never treat an approval on an older commit as terminal. A
  submitted approval is current only when its reviewed commit matches a
  freshly read head. For a top-level approval comment with no reviewed commit,
  compare its `updatedAt` when edited, otherwise its creation time, with the
  recorded activation boundary for this exact head, and require that the head
  remains unchanged across the approval check; otherwise request a fresh
  review. A commit's authored or committed timestamp is not an activation
  boundary because a locally old commit can be pushed after the comment.
- If there is no new activity, make no repository or GitHub changes and keep
  the update terse. If the latest review request still has no subsequent Codex
  verdict about 30 minutes after that request's activation boundary, pause and
  ask the user to invoke the repository's recovery trigger instead of polling
  indefinitely.
- Classify every new or edited submitted review, every new or edited top-level
  conversation comment, every new or edited inline thread comment, and every
  reopened inline thread before recording it as handled. For a reopened thread,
  re-evaluate its underlying feedback against the current head even when no new
  comment was added. For actionable feedback from any source, resume the author
  loop in this same task: assess every finding, make agreed mechanical fixes,
  verify
  them, push, and reply to each addressed thread with the fix and commit SHA.
  Follow the repository's own author-side review rules for round caps and scope.
- A non-approving Codex review with no top-level finding and no inline finding
  is a content-free pass, not approval. Record its review ID, then allow one
  exceptional retry on the same head even though that head was already
  requested. Track the retry separately from ordinary requested-head state and
  restart the no-verdict timer from the retry trigger's activation boundary.
  If the retry is also content-free, pause and ask the user instead of polling
  forever.
- Use exactly one repository-configured transport for the next Codex review.
  In a repository with an `awaiting-codex-reping` CI gate, push the fix first,
  re-read the new head, then advance the durable `codex-round-N` label and arm
  the gate for that new head before letting the green-CI workflow post the bare
  trigger. Immediately re-read the head, gate label, and workflow-produced
  triggers after arming; if the head moved, disarm the stale gate and restart
  this sequence for the new head. Record whether this gate activation advanced
  the counter; some unreviewed pushes require a gate without representing a new
  review round. In a repository without that gate, advance
  the round after the fix head is green and let the originating task post the
  exact bare trigger itself; never embed the trigger in prose, because a mixed
  comment enters Codex action mode. Absent means round zero; remove the previous
  label, create/apply `codex-round-{N+1}` exactly once for the handled review,
  and do not request another review when the counter is already 25.
  Immediately before either transport claims the request, re-read both the head
  and the latest required-check states and abort if either changed. Record the
  resulting trigger comment ID and creation time as this head's activation
  boundary. Apart from the single content-free retry, request exactly once per
  head. If checks fail, diagnose and fix failures caused by the PR first.
- Treat a merge conflict that prevents checks from starting, a cancelled or
  timed-out required check, and a required status that remains missing as
  recovery states rather than ordinary waiting. Follow the repository's
  mechanical recovery procedure when authorized; otherwise pause and ask the
  user. Do not poll a terminal or permanently blocked CI state forever.
- Stop and ask the user instead of changing code when the author disagrees
  with a finding, a finding needs an architectural or product decision, the
  loop oscillates, or the repository's hard stop is reached.

Pause the scheduled task when any authenticated, current-head approval matches
the repository's recognized final-approval rules, the PR merges or closes,
ownership is handed off, the user asks to stop, or a disagreement needs the
user's decision. Before pausing a still-open Codex-owned PR:

- Record the current round label and whether this gate activation advanced it,
  disarm any `awaiting-codex-reping` gate, and re-read trigger comments around
  that removal. If removal conclusively won the claim race, no trigger exists
  for that cycle, and this activation advanced the counter, remove the unused
  current round label, then reapply the previous label only when its number is
  greater than zero. If this activation did not advance the counter, leave its
  existing round label unchanged. If the outcome is uncertain or a trigger
  landed, also leave the counter unchanged and report that state.
- If a trigger is already inbound, keep the scheduled task active for that one
  final delivery only to record, classify, and report its events. A stop or
  handoff grants no authority to implement its findings, push, reply, advance
  the round, or re-arm a review transport; leave actionable findings for the
  user or new owner.
- For a handoff to Claude, establish the replacement author through a transport
  the repository permits before pausing. On a non-`codex/*` branch, remove
  `codex-only` and post a fresh `@Claude` comment; label removal alone does not
  wake Claude. If the repository deliberately makes PR-triggered Claude runs a
  no-op on `codex/*`, a comment is not a handoff: open or transfer to a supported
  Claude Remote session and confirm that it owns the PR. Do not report the
  handoff complete until the replacement owner is active.

Report the terminal reason in this task. Never merge merely because the review
loop approved the PR.

This is a polling bridge, not an event webhook: the desktop app and computer
must remain running, and wake-up latency is bounded by the chosen interval.
Event-driven delivery to an existing desktop task remains deferred until the
product exposes a supported desktop event trigger or task-resume transport.
