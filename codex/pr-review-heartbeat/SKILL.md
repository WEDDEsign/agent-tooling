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

1. Establish the owner signal before a ready PR can trigger Claude. Open new
   Codex-owned ready PRs on `codex/*`. If another branch name is required, open
   the PR as a draft, apply `codex-only`, and only then mark it ready. Adding
   the label after opening a ready PR can lose the initial wake-up race. When
   adopting an existing PR, establish any missing signal and check whether a
   Claude author task is already active; coordinate that handoff before both
   authors can act.
2. Create a recurring task **inside the current chat**, normally every two
   minutes, named for the repository and PR number. Do not create a standalone
   scheduled task or a new Codex task. Scheduled runs cannot request an
   interactive permission escalation: configure an unattended permission
   profile or narrow command rules for authenticated GitHub network access,
   plus the read and authorized write operations this loop needs. Verify an
   authenticated PR read from a scheduled run and the configured write scopes
   before relying on wake-up; if access fails, report the blocked loop.
3. Give the scheduled task a durable prompt containing the repository, PR
   number, branch, review-transport checks, merge-only checks, and the stop
   conditions below. A deliberately deferred full-validation status belongs
   only to the merge gate.
4. Record a head-activation boundary in this task. For an initially ready PR,
   use the PR creation event. A draft has no active review boundary: when it
   becomes ready, use its `ready_for_review` event. For every later head, use
   the database ID and creation time of the bare review-trigger comment posted
   for that exact head. If a direct trigger races a push but the resulting
   review targets the new head, use no earlier than the time that new head was
   first observed as its boundary; the pre-push trigger cannot authenticate
   an approval for the new commit.
   When adopting an existing head that has neither boundary, first read and
   classify its existing reviews, comments, and unresolved threads, process any
   actionable feedback, and only then ledger those events. Record the adoption
   check's time as that head's fallback boundary. This boundary makes a later
   comment-based approval attributable to the current head and starts the
   no-verdict timer; never accept a pre-adoption approval signal against it.
   After classifying existing activity, if no qualifying current-head verdict
   or live Codex request remains, mark one adoption request for this head. This
   includes empty review history and a rejected pre-boundary approval. Track
   it separately from the ordinary per-head request and content-free retry;
   submit it through the common request procedure below.

Each scheduled run should:

- Treat a draft PR as an inactive review loop. Poll only enough state to notice
  that it became ready; do not run the no-verdict timer, advance a round, arm a
  gate, or request review while it is draft. If it became draft while a gate was
  armed, disarm it and settle any in-flight CI-green, sweep, or review-first
  controller claim using the stop procedure below. On the ready transition, record that event as the
  activation boundary and use its event ID as a fresh same-head review key.
  If automatic Codex review is already running, track it; otherwise request
  once after transport checks pass.
- Query the PR state, draft status, live head branch ref, labels, mergeability,
  current head SHA, review-transport and merge-only checks, all submitted
  reviews (including author, association, and `lastEditedAt`), top-level PR
  conversation comments (including author, association, and `updatedAt`), and
  all inline review threads and comments (including each comment's author,
  association, and `updatedAt`). For review-first PRs, also query reactions
  on the Codex bot's review summary and the dedicated App's authenticated
  controller checkpoint for this PR and head. A checkpoint-pointer comment is
  only a locator, never evidence. Refresh summary reactions on every poll:
  a new reaction need not change the comment's `updatedAt`. Also query the
  PR timeline's `ReviewRequestedEvent` and
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
- On every run, require the Codex owner signal (`codex/*` or `codex-only`)
  before unattended author actions. If it disappeared, enter the full stop
  procedure below: disarm the gate, settle in-flight CI-green, sweep, and
  review-first claims, and account for any trigger already inbound before
  pausing or coordinating ownership. A claimant may have removed the label
  already, so label absence alone cannot prove delivery stopped. Do not make
  further author changes during the handoff; another author may be active.
- Apply the repository's approval rules only after authenticating the signal.
  A submitted review supplies its reviewer identity, but a token-bearing
  submitted review still requires authorization: reject the PR author and
  require either an explicitly requested reviewer or a non-author repository
  role the repository recognizes as a reviewer (for example `OWNER`, `MEMBER`,
  or `COLLABORATOR`). Apply the same authorization to a top-level approval
  token. For every repository-recognized Codex approval form, accept the
  configured Codex review bot identity without requiring a review request or
  repository role, and reject that Codex-specific form from any other author.
  For an edited review-first summary, use the controller's clean-verdict rule:
  require a fresh Codex-bot +1 reaction after the summary update or its
  authenticated `approved_summary` receipt, with no later findings or
  invalidating edit. A completed summary alone is not approval.
- Compare the reviewed commit with the current PR head. If they differ, report
  the review as stale and confirm that each finding still applies before
  changing code. In classic mode, never treat an approval on an older commit
  as terminal. In review-first mode, accept an older clean review for a moved
  head only when the authenticated controller has accepted its unchanged-diff
  carry-over for that current head; do not infer carry-over from the diff
  yourself. A submitted approval is current only when its reviewed commit matches a
  freshly read head. For a top-level approval comment with no reviewed commit,
  compare its immutable creation time with the recorded activation boundary
  for this exact head. An edit cannot renew approval for a later head; if the
  comment was edited, require a fresh unedited approval comment or submitted
  review. Require that the head remain unchanged across the approval check;
  otherwise request a fresh review. A commit's authored or committed timestamp
  is not an activation boundary because a locally old commit can be pushed after the comment.
- If there is no new activity, make no repository or GitHub changes and keep
  the update terse. If the latest review request still has no subsequent Codex
  verdict about 30 minutes after that request's activation boundary, pause and
  ask the user to invoke the repository's recovery trigger instead of polling
  indefinitely.
- Classify every new or edited submitted review, every new or edited top-level
  conversation comment, every new or edited inline thread comment, and every
  reopened inline thread before recording it as handled. For a reopened thread,
  re-evaluate its underlying feedback against the current head even when no new
  comment was added. Authenticate feedback before granting author actions:
  accept findings from the configured Codex review bot, an explicitly requested
  reviewer, or a non-author repository role recognized as a reviewer. Report
  feedback from all other accounts for user triage; do not let their comments
  drive unattended edits, pushes, or replies. For authorized actionable
  feedback, assess each finding, make agreed mechanical fixes in this task,
  verify them, push, and reply to each addressed thread with the fix SHA.
  Follow the repository's own author-side review rules for round caps and scope.
- A non-approving Codex review with no top-level or inline finding is a
  content-free pass, not approval. Record its review ID. If its reviewed commit
  differs from a fresh head, treat it as stale and use the ordinary new-head
  path. Otherwise mark one exceptional retry keyed by that review ID and head.
  A second content-free response on the same head is a stop for the user.
- Use one request procedure for an ordinary new head, a ready transition,
  an adoption re-review, and a content-free retry. Keep a ledger key of
  (head SHA, reason, and its review or event ID). A request already in flight
  for that key counts as requested; never issue a duplicate. Wait for any
  other live Codex review request or bot summary marked running on the same
  head to finish before considering another; an unrelated human reviewer
  request does not block this transport.
  For direct requests, wait for the configured review-transport checks to pass.
  Before posting, settle all configured gate claimants: in-flight CI-green
  runs for this PR across current and earlier heads, all in-flight
  sweeps repository-wide, and any in-flight review-first controller run or
  durable current-head `request_intent`. A removed label alone does not
  prove delivery is over.
  If a claim is uncertain, wait or pause rather than post.
  Immediately before posting, re-read PR state and draft status, live head
  branch ref and SHA, owner labels, mergeability, transport checks, trigger
  comments, live Codex requests and running bot summary, submitted reviews,
  top-level comments, inline threads, and reviewer request/removal events. In
  review-first mode, refresh summary reactions and the authenticated App-owned
  controller checkpoint, including its approval receipt and carry-over state;
  a summary can gain a completion reaction without a comment edit. Apply all
  these events chronologically before authenticating new feedback or approval.
  Require an open, ready, mergeable PR with the Codex owner signal, the chosen
  head, green transport checks, no terminal verdict or controller-recognized
  clean carry-over, and no armed
  gate, claimant, or equivalent request. If any condition changed, classify
  the new events and restart or stop; do not post from the old snapshot.
  In classic direct mode, post only a standalone `@codex review` comment;
  prose with the trigger enters Codex action mode. Record its comment ID and creation time as the activation
  boundary and restart the no-verdict timer.
  Immediately re-read the head after posting and track the request until its
  reviewed commit is known. If the head moved, do not spend an old-head retry
  on the new head. If the review targets the new head, require its transport
  checks to pass, set its activation boundary no earlier than first observing
  that head, and do not arm a duplicate gate; if it targets the old head,
  treat it as stale and use the normal new-head gate.
  A ready transition, content-free retry, and adoption re-review use this
  direct transport after green checks in classic mode, even if the repository
  normally uses a CI gate. If a review-first controller manages this PR, check
  its authenticated current-head decision before arming a request: if it
  carried a prior clean review across an unchanged diff, let final validation
  proceed without a new review or round. Arming a gate would invalidate that
  evidence. Otherwise use its durable gate and intent for every request,
  including adoption and retries; a direct comment would not update the
  controller's requested-head state. If controller mode is uncertain, or a claimed carry-over cannot be
  verified, pause rather than post.
  Follow the repository's round policy for each exception: an empty pass
  counts as a round where the local runbook requires it, while an adoption re-review need
  not count unless that runbook says so. Apply the hard stop before retrying.
  In classic direct mode, do not leave a label-only gate armed for a same-head
  retry after CI has already completed.
  Round cap, on every path: before advancing a counter, arming a gate, or
  requesting review, compare the round this review would start with the
  repository's author-side cap (its own hard stop, never above 25). The
  workflow backstop can be higher than that cap and is not the limit to use.
  At the cap, do not arm or request: run the stop procedure below, apply the
  repository's owner-facing stop label (for example `needs-owner`) with any
  CI gate removed first, post its required stop note, and pause.
  For an ordinary new head in a classic-mode repository with an
  `awaiting-codex-reping` CI gate, push the fix, re-read the head, advance
  `codex-round-N` exactly once for the handled review. Immediately before
  arming the gate, re-read PR state, draft status, live branch ref and SHA,
  owner labels, mergeability, and transport checks. Require the same open,
  ready, mergeable, Codex-owned head with green transport checks. If ownership
  disappeared, enter the full stop procedure; if the head changed, restart.
  Only then arm the gate. Arm promptly, but if CI has already completed,
  dispatch the configured sweep when authorized or wait for its scheduled
  recovery; the past CI completion will not fire again. The green-CI
  workflow rechecks the head and required review-transport checks after it
  claims the label. Its label claim prevents another gated trigger; the task
  must not post a direct trigger while that gate is armed or being claimed.
  After arming, re-read the head, owner signal, gate label, and workflow-produced
  triggers; if the head moved, disarm the stale gate and restart. If ownership
  disappeared, enter the full stop procedure. Some unreviewed pushes
  require a gate without a new round.
  In a repository without that gate, use the direct procedure after CI is
  green. Absent means round zero; remove the previous round label and apply
  `codex-round-{N+1}` only once for an addressed review, subject to the round
  cap above. Diagnose PR-caused check failures before requesting review.
- Treat a merge conflict that prevents review-transport checks from starting,
  a cancelled or timed-out review-transport check, and a review-transport
  status that remains missing as recovery states rather than ordinary waiting.
  A deliberately deferred full-validation status is expected until the
  repository's merge-readiness gate runs; do not recover it during iteration. Follow the repository's
  mechanical recovery procedure when authorized; otherwise pause and ask the
  user. Do not poll a terminal or permanently blocked CI state forever.
- Stop and ask the user instead of changing code when the author disagrees
  with a finding, a finding needs an architectural or product decision, the
  loop oscillates, or the repository's hard stop is reached.

Pause the scheduled task when any authenticated, current-head approval matches
the repository's recognized final-approval rules, the PR merges or closes,
ownership is handed off, the user asks to stop, or a disagreement needs the
user's decision. Before pausing any Codex-owned PR, including one that just closed:

- Record the current round label and whether this gate activation advanced it,
  disarm any `awaiting-codex-reping` gate, then inspect trigger comments,
  all in-flight CI-green runs that could target this PR (including runs for
  earlier heads), all in-flight sweeps repository-wide, and any configured
  review-first controller run or durable current-head `request_intent` on an
  open, ready PR. On a closed, merged, or draft PR, settle live runs and any
  inbound trigger, then disregard a stored `request_intent`: it cannot deliver
  while the PR is inactive, and the controller may leave it behind. Do not wait
  for that intent to disappear or restore its gate. Older CI-green runs can restore a
  gate after a head move; sweeps and controllers
  may attach to the default branch. A missing label and absent trigger do not prove that removal won: a run may already have
  claimed the label and be about to post. Wait for those runs to settle, then
  re-read and disarm any gate they restored before pausing. Recheck for another
  in-flight claim. Only if no run claimed the gate, no trigger exists for that
  cycle, and this activation advanced the counter, remove the unused current
  round label and reapply the previous label when its number is greater than
  zero. Otherwise leave the counter unchanged. If a claim or restoration
  cannot be ruled out, keep the task active for final delivery and report the
  uncertain outcome.
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
