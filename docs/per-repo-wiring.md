# Per-repo wiring

How each repo opts into the shared ceremony. Three independent layers — do them
in this order; each works on its own.

```
1. Labels          (gh, instant)          ── no secrets, no PR review needed
2. Plugin          (.claude/settings.json) ── client-side; commit via PR
3. Workflows       (caller YAML + PAT)     ── server-side; needs `workflow` scope + a PAT
```

## Layer 1 — Labels (do first, unblocks everything)

The hooks/skills/workflows reference label names exactly; a missing label makes
them silently no-op. Apply the canonical taxonomy:

```sh
for r in Mentra VectraIQ intelligence-core NordScope; do
  labels/sync-labels.sh "WEDDEsign/$r"
done
```

Idempotent and additive — it updates colors/descriptions to match and never
deletes anything (the legacy `codex` label is left as-is).

## Layer 2 — The ceremony plugin (client-side)

Commit `.claude/settings.json` (see `templates/settings.json`) to each repo via
a PR. Opening the trusted repo then prompts each teammate to install the plugin;
the `codex-review-triage` skill, `/open-pr`, `/delegate-to-codex`, `/fanout`, and
the security-surface guard become available.

**Dogfood first (recommended before publishing agent-tooling):** instead of the
GitHub source, run `/plugin marketplace add /path/to/your/agent-tooling`
once, or drop `templates/settings.local-dogfood.json` at
`.claude/settings.local.json`. Prove a real PR through the loop, then switch to
the GitHub source and commit `settings.json`.

## Layer 3 — Server-side workflows

Copy the matching `templates/caller-workflows/*.caller.yml` into each repo's
`.github/workflows/` (drop the `.caller` from the filename), fill in the
repo-specific values, and add the `CEREMONY_PAT` secret.

### Two prerequisites (call these out before starting)

- **`CEREMONY_PAT` secret.** The wake-up comments must be authored by a *human*
  identity — the Claude GitHub App ignores mentions from `github-actions[bot]`
  (bot-to-bot loop guard; NordScope diagnosed this in its PR #305). Create one
  **classic** PAT carrying the **`repo`** scope, add it as the `CEREMONY_PAT`
  repo/org secret. Describe it by what it can do rather than what it was meant
  for: classic `repo` includes contents read/write, so the same token works as
  `actions/checkout`'s `token` and for fetch and push — the older wording here
  ("fine-grained, pull-requests: write") understated that and reads as a
  blocker on any change that hands it to checkout. It is not one. Note this
  bullet and the next were mutually exclusive as written: `repo` is a classic
  scope that fine-grained tokens do not have.

  NordScope reads `secrets.CEREMONY_PAT` and always has. An earlier version of
  this page said it already had the token as `NordscopePAT` and offered a
  mapping for it; no secret by that name has ever existed there, so following
  that advice would have wired the callers to an empty secret.
- **`workflow` token scope.** Pushing files under `.github/workflows/` requires
  a token with the `workflow` scope. The PAT currently in use here has `repo`
  but **not** `workflow`, so these caller files must be pushed with a
  workflow-scoped token (or committed through the GitHub UI). This is the one
  hard blocker for landing the server-side plane via automation.

### The re-ping sweep (add it with wake-on-ci-green, not instead of it)

`wake-on-ci-green` is **edge-triggered**: it reads `awaiting-codex-reping` at
the instant a CI workflow completes — roughly three minutes after a push on a
small PR. A session that writes the label a moment later loses the race. The
evaluation has already run and skipped, and nothing re-runs it, because no
further CI completion is coming. There is no error and no retry; the loop just
parks until a human notices and re-pings by hand. Documenting the correct order
("label first, then push") cannot close a race — a session can follow the
instruction and still lose.

`sweep-stalled-repings` is the **level-triggered** counterpart. On a schedule it
asks "which PRs are, right now, labelled and green and un-re-pinged?", so
whenever the label lands the next sweep picks it up. Copy its caller alongside
the `wake-on-ci-green` one and give both the **same** `required_checks` value;
a narrower list in the sweep would re-ping against checks the event path still
considers incomplete.

It is deliberately a `schedule`, not a `pull_request: labeled` trigger. The
labeled version was built and withdrawn (agent-tooling#6): `pull_request` runs
the caller from the PR's own branch with secrets still provided, so any PR that
edited its own caller would run the edited file with `CEREMONY_PAT` available.
Cron runs only from the default branch, so PR-authored workflow code never
executes.

What it fixes outright: the missed-label race, and a content-free Codex pass
arriving when CI is already green (no further CI completion is coming, and
label writes do not emit `workflow_run`). What it only **surfaces**, in the run
summary, because they still need a person: a merge conflict (CI cannot run at
all, so there is nothing green to re-ping against) and required checks that were
cancelled or timed out (`wake-on-ci-red` fires on `failure` only).

### Reusable-workflow notes

- **Each caller needs its own `permissions:` block.** A called (reusable)
  workflow cannot request more permission than the caller's token grants, and
  most repos default to a read-only `GITHUB_TOKEN`. Without the block the run
  fails at startup with `startup_failure` and no logs. The caller templates
  already include the right block — keep it.
- **Reusable workflows need a published repo.** GitHub Actions can only `uses:`
  a reusable workflow from a real repo (public, or private on Team+) — there is
  no local-path equivalent, so Layer 3 can't be dogfooded purely locally. Since
  `agent-tooling` is public, any repo (private included) can call it. Pin callers
  to a tag (`@v0.1.0`) rather than `@main`.

## Per-repo deltas

| Repo | Labels | Plugin | Workflows | Notes |
|---|---|---|---|---|
| **NordScope** | sync (reconciles its ad-hoc scheme) | adopt plugin | **already runs these as standalone YAML** — migrate to the reusable callers to de-duplicate, no behavior change | The source of truth. Its `NordscopePAT` → map to `CEREMONY_PAT`. Required checks: `typecheck-and-build`, `pytest`, `banned-strings`; CI workflows: `frontend-ci`, `backend-tests`, `migration-guardrails`. |
| **Mentra** | sync (has the taxonomy; fixes colors) | **pilot** | gains the full autopilot it lacks today (has only `issue-link`) | Required checks: `Backend (pytest)`, `Backend integration (Firestore emulator)`, `Frontend (tsc + build)`; CI workflows: `CI`, `E2E`. Keep its 21 eval workflows + `issue-link` as-is (domain). |
| **VectraIQ** | sync (**had only a bare `codex`**) | adopt plugin | gains autopilot from zero | **Biggest gap: its `CLAUDE.md` has 0 Codex mentions.** Before wiring, add the Codex collaboration model to its `CLAUDE.md` (copy Mentra's section). Confirm CI workflow + check names (`backend-ci`, `frontend-ci`). |
| **intelligence-core** | sync (has taxonomy w/ different colors) | adopt plugin | gains autopilot from zero | Shared-contracts repo. Required check: from `test.yml`. Its domain tooling (schema-evolution skills) stays local. |

## What is NOT shared (stays repo-local)

Domain tooling: Mentra's eval-stage knowledge + `<Page>`/`<Section>` layout +
tenancy scaffolds; VectraIQ's enrichment domain; intelligence-core's schema
contracts; NordScope's ESRS/ESG domain. Promote a local skill into this repo
only when a **2nd** repo needs it.
