# Review-first CI (opt-in)

The composite action `actions/review-first-ci` adds a shared controller for
Claude- and Codex-owned PRs. It is inert until a consumer wires it in; the
existing reusable workflows keep their current defaults. Do not move an
existing release tag to roll this out. Consumers pin a reviewed commit, then
adopt a new release after the pilot is proven.

## Contract

An eligible PR receives one initial full validation. After it passes, review
fixes can be pushed without repeating the expensive workers. A clean Codex
review of the current head starts final full validation. `merge-validation`
passes only after those exact runs, every other required check, and the current
review are successful. The controller never merges.

GitHub required checks certify a commit, not a separate approval for each PR.
A second PR at the identical commit targeting the default branch may reuse a
successful check; opening it does not revoke validation. Strict up-to-date
rules still apply, and a changed commit needs fresh validation. Controller
phase and review bookkeeping remain per PR; this is not a promise to suppress
every duplicate workflow. Classic PRs retain their existing CI policy.
The required gate is one App-owned check per commit, bound to the validated
default-branch base. Other PRs' state writes cannot reset it; the certifying
PR's withdrawn review still invalidates the shared result. Non-default-target
PRs cannot publish or replace this certificate.

The consumer supplies a JSON mapping of workflow filenames to test job names.
Each worker accepts `pr_number`, `expected_head`, `expected_base`, and `ticket`
as workflow-dispatch inputs and uses `review-first-TICKET` as its run name.
Dispatch uses the target base branch's trusted workflow code, and admission
requires its `GITHUB_SHA` to match the requested base. A base advance makes
that dispatch stale. Admission verifies the request against controller state;
the worker checks out
the PR merge ref and verifies both parents before running any project code.
Skipped jobs, failed retries, stale dispatches, and an old head's review are
not successful validation. A new push invalidates final validation. If initial
validation failed, the next push repeats initial validation.

`admit` and `verify-checkout` need read-only repository access. `reconcile`
requires an installation token from a **dedicated GitHub App**, with
Actions/checks/issues write and contents/pull-requests/statuses read. Pass its
numeric ID as `controller-app-id` to both admission and reconciliation. The
controller verifies the App identity on stored state and every check-write
response; GitHub Actions App 15368 and missing/mismatched identities are
rejected. Its separate review
token posts the bare review trigger under a human identity.

Keep the App's private key only in a protected GitHub environment restricted
to the default branch (selected branch name, no tags). Do not use a repository
or organization secret for this key. Mint a short-lived installation token
scoped to the consumer repository in a metadata-only controller job. Use a
pinned token action and let it revoke the token afterward. Never check out PR
code, download PR artifacts, or run project scripts in that job. Repository
administrators and changes merged into the default branch remain trusted.

PR review and inline-comment events run at PR refs and must not receive the
protected environment. Use a credential-free notification workflow; its
completion wakes the controller via `workflow_run` on the default branch.
An untrusted notification may select a PR number to re-read, never code, state,
a review verdict or a validation result. Serialize all controller entrypoints.
Keep the existing author routing: Codex-owned PRs must not wake Claude.

## Prerequisites and modes

Before tests can be deferred, the effective branch rules must require
`merge-validation` from that dedicated App (its configured integration ID),
with strict up-to-date checks. The shared GitHub Actions identity is not a
valid source for this gate. Preserve every existing required check. Admission falls
back to normal CI if these prerequisites cannot be established.

Before opting in, create any missing repository labels: `review-first-ci`,
`review-first-ci-active`, and `ci-always`. Also provision the existing review
transport's `awaiting-codex-reping` and `codex-round-1` through `codex-round-6`
labels (or its larger configured range). The controller associates labels;
it does not create repository label definitions. Only the controller applies
`review-first-ci-active` to PRs.

The caller passes its `CI_REVIEW_MODE` repository variable:

- Empty, `classic`, or unrecognized: existing CI on every push.
- `pilot`: same-repository, ready PRs labelled `review-first-ci`.
- `enabled`: also includes `codex/*`, `claude/*`, and `codex-only` PRs.
- `ci-always` opts a PR out. Drafts, fork PRs, and PRs targeting a branch other
  than the repository default retain normal CI. Controlled dispatch also
  rejects non-default targets; a PR author cannot nominate an unprotected
  feature branch as trusted workflow code.

Opting an active default-branch pilot out starts classic validation for its
current head, so previously deferred jobs do not wait for another push.
Unrelated required checks follow GitHub's success/neutral/skipped semantics;
the controller's initial, final and restored test workers must actually succeed.

`review-first-ci-active` is controller-owned. Pass it as `excluded_label` to
both the legacy CI-green transport and its scheduled sweep. The author still
pushes, verifies the new head, replies to findings, and arms
`awaiting-codex-reping`; the controller requests the next review without
waiting for the deferred workers. Keep the round counter accurate. The
controller refuses new requests at round six; narrower repository caps still
apply to the author.

Codex's authenticated current-head completed summary plus a fresh thumbs-up
and no inline findings is a clean result. A submitted approval, standalone
`APPROVED` verdict, or fixed no-major-issues review on that head also qualifies. No template-driven retry
is needed. The summary's edit can precede its reaction; reconciliation gives
that reaction a short opportunity to arrive, then leaves validation pending.
Once observed, the authenticated summary/reaction result is recorded in the
App-owned checkpoint, bound to the head, review activation and exact summary.
The reaction is a completion signal, not a continuing approval switch: removing
it later does not revoke the recorded review. A changed/deleted summary, new
findings, dismissed review or new activation invalidates that receipt. This
avoids relying on reaction-change events, which Actions does not deliver.

## Events and recovery

Reconcile on PR updates, Codex summary creation/edits/deletion, review events, and worker
completion. Serialize controller invocations. Invoke the same controller from
the consumer's existing periodic recovery workflow; do not add another polling
tier. It rereads GitHub rather than trusting stale event snapshots.

Controller state is JSON in a dedicated App-owned `review-first-state` check
output, bound to the repository, PR number and head. These non-required
checkpoints finish with a neutral conclusion; only `merge-validation` gates
the commit. GitHub restricts check
updates to the creating App; a PR's Actions token cannot change this record.
Comment bodies are **not** state. A small pointer comment lets the next head
locate a prior checkpoint; every pointer is dereferenced through the Checks
API and authenticated before use. The current head's checkpoint always wins
over old pointers. Copying/editing comments or creating same-name Actions
checks cannot certify validation. Deleting all pointers may repeat initial
validation on a later head; it cannot invent a passing baseline.

This replaces the unreleased v1 Actions-comment format. V1 state is ignored;
there is no migration that imports unauthenticated data. A prior pilot must
be restored to classic CI before changing its gate source. No production v1
pilot is assumed or required by this implementation.

The controller records dispatch intent before issuing requests. If a dispatch
partially fails, it reports an error rather than silently issuing duplicate
full suites. Use the rollback path below to recover. Normal pending workers
are observed without being redispatched.

## Rollback

1. Set the consumer's `CI_REVIEW_MODE` to `classic`.
2. Run its controller manually with `restore=true`, optionally selecting a PR.

The restore operation removes the active-transport label and starts full CI
for existing pilot PRs. Unrelated PRs are not restarted. It keeps the merge
gate pending until validation passes. Future pushes use classic CI, and the
legacy review transport resumes. There is no need to remove required checks,
force-push, create empty commits, or move a release tag. Keep this recovery
path available until every active pilot has returned to classic mode before
reverting consumer wiring.

## Validation

Run `python -m unittest discover -s actions/review-first-ci -p 'test_*.py' -v`.
The suite exercises the two validation phases, failures, skipped jobs, stale
heads/reactions, both author types, and restoring an in-flight pilot. The
reported forged-state bypass is replayed against the real state adapter:
forged Actions comments/checks cannot reuse initial runs as final validation.
Tests also cover cross-PR pointers, replayed pointers, mismatched App identity,
non-default targets and edited inline findings. Keep
`scripts/test-decision-logic.sh` and the heartbeat tests green for consumers
that have not opted in. A live consumer pilot remains necessary before
enabling the mode broadly.
