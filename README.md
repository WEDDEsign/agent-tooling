# agent-tooling

Shared **ways-of-working** for the WEDDEsign intelligence stack — **Mentra**,
**VectraIQ**, **intelligence-core**, and **NordScope**.

This repo is the single source of truth for the *ceremony* every repo runs:
the Codex review autopilot, PR/issue-link discipline, the task-routing labels,
and the security-surface guard. Write a rule once here; every repo inherits it.

## Why this exists

The four repos share an identical workflow — GitHub PRs, Codex review rounds,
`Closes #N` issue-link discipline, task-routing labels — but that knowledge
lived as **passive prose** duplicated across four `CLAUDE.md` files (NordScope
640 lines / 61 codex mentions → VectraIQ 174 / **0**). It was re-interpreted
every session, drifted between repos, and was **not executable**: nothing
*enforced* it.

This repo makes the ceremony executable and shared. The guiding rule (from the
toolbox-thinking model that kicked this off): **rank tooling by
`frequency × friction removed`, and prefer boring infrastructure over flashy
demos.** Every asset here is high-frequency ceremony, so it is shared-by-default.

## The two planes

Ceremony runs on two planes. You need both — neither covers the other's cases.

| Plane | Vehicle | Fires when | Covers |
|---|---|---|---|
| **Client-side (sync)** | The `ceremony` **plugin** — skills, commands, hooks | A human is in an interactive Claude Code session | Guidance the agent *follows* + guards it *cannot skip* locally |
| **Server-side (async)** | **Reusable GitHub workflows** in `.github/workflows/` | No session is open — Codex finishes a review, CI turns green/red | Advancing the PR loop while nobody is watching |

The plugin cannot advance a PR when no session is open; the workflows cannot
scaffold a page or run an eval before push. Ship both.

## Layout

```
agent-tooling/
├── .claude-plugin/marketplace.json     # catalog (one marketplace, one+ plugins)
├── plugins/
│   └── ceremony/                        # the shared client-side plugin
│       ├── .claude-plugin/plugin.json
│       ├── skills/
│       │   ├── codex-review-triage/     # the Codex autopilot loop (extracted from NordScope)
│       │   └── open-pr/                 # Closes #N + test-plan discipline
│       ├── commands/                    # /delegate-to-codex, /fanout
│       └── hooks/                       # security-surface-guard (PreToolUse)
├── .github/workflows/                   # reusable (workflow_call) ceremony — the server-side plane
│   ├── wake-on-codex-review.yml
│   ├── wake-on-ci-green.yml
│   ├── wake-on-ci-red.yml
│   ├── auto-resolve-review-threads.yml
│   └── cleanup-stale-codex-labels.yml
├── templates/caller-workflows/          # thin per-repo callers that invoke the reusable workflows
├── labels/                              # canonical label taxonomy + gh sync script
└── docs/per-repo-wiring.md              # how each repo opts in
```

## Install (per consuming repo)

1. **Labels** (one-time): `labels/sync-labels.sh WEDDEsign/<repo>`
2. **Plugin**: commit `.claude/settings.json` with the marketplace + `enabledPlugins`
   (see `docs/per-repo-wiring.md`).
3. **Workflows**: copy the matching `templates/caller-workflows/*` into the repo's
   `.github/workflows/`, set the repo-specific check names, add the `CEREMONY_PAT`
   secret.

Full per-repo steps and the VectraIQ / intelligence-core / NordScope deltas are
in [`docs/per-repo-wiring.md`](docs/per-repo-wiring.md).

## Governance

- **Promotion rule:** domain tooling stays repo-local; ceremony is
  shared-by-default; a local skill graduates here on its **2nd consumer**.
- **Source of truth is NordScope's proven loop** — the autopilot here was
  extracted from it, not invented. Improve it here, not in a fork.
- **Definition of done (a shared asset):** used in real work ≥1×; no hardcoded
  repo specifics (everything repo-specific is an input/config); namespaced.
- **Precedence hygiene:** shared skills are namespaced `ceremony:<skill>`; never
  create a same-named skill in a repo's local `.claude/`.
- **Ownership:** one owner for versioning/releases of this repo.

## Dogfood before publish

Per plan: prove this locally before pushing to GitHub. Add it as a local-path
marketplace — `/plugin marketplace add ./agent-tooling` — wire the Mentra pilot
against it, run a real PR through the loop, *then* publish to
`WEDDEsign/agent-tooling` and flip repos to the GitHub source.
