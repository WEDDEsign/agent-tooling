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
  fine-grained PAT (pull-requests: write on these repos), add it as the
  `CEREMONY_PAT` repo/org secret. NordScope already has this as `NordscopePAT`
  — either rename, or map it in the caller (`CEREMONY_PAT: ${{ secrets.NordscopePAT }}`).
- **`workflow` token scope.** Pushing files under `.github/workflows/` requires
  a token with the `workflow` scope. The PAT currently in use here has `repo`
  but **not** `workflow`, so these caller files must be pushed with a
  workflow-scoped token (or committed through the GitHub UI). This is the one
  hard blocker for landing the server-side plane via automation.

### Reusable-workflow note

GitHub Actions can only `uses:` a reusable workflow from a **published** repo
(private is fine) — there is no local-path equivalent. So Layer 3 cannot be
dogfooded locally; it goes live only after `WEDDEsign/agent-tooling` is pushed.
Pin callers to a tag (`@v0.1.0`) rather than `@main` once published.

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
