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
needs Actions/checks/issues write and contents/pull-requests read. Its separate
review token posts the bare review trigger under a human identity. Run it as
trusted metadata-only code; do not check out the PR with those credentials.
Keep the existing author routing: Codex-owned PRs must not wake Claude.

## Prerequisites and modes

Before tests can be deferred, the effective branch rules must require
`merge-validation` from GitHub Actions (integration ID 15368), with strict
up-to-date checks. Preserve every existing required check. Admission falls
back to normal CI if these prerequisites cannot be established.

The caller passes its `CI_REVIEW_MODE` repository variable:

- Empty, `classic`, or unrecognized: existing CI on every push.
- `pilot`: same-repository, ready PRs labelled `review-first-ci`.
- `enabled`: also includes `codex/*`, `claude/*`, and `codex-only` PRs.
- `ci-always` opts a PR out. Drafts and fork PRs retain normal CI.

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

## Events and recovery

Reconcile on PR updates, Codex summary creation/edits, review events, and worker
completion. Serialize controller invocations. Invoke the same controller from
the consumer's existing periodic recovery workflow; do not add another polling
tier. It rereads GitHub rather than trusting stale event snapshots. A small
controller-state comment authored by GitHub Actions records the PR's phase,
dispatch tickets, baseline, and review activation. Human-authored copies are
ignored. Do not manually edit or delete that comment during a pilot.

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
heads/reactions, both author types, and restoring an in-flight pilot. Keep
`scripts/test-decision-logic.sh` and the heartbeat tests green for consumers
that have not opted in. A live consumer pilot remains necessary before
enabling the mode broadly.
