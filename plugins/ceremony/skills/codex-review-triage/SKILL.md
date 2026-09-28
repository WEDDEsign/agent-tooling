---
name: codex-review-triage
description: Run the Codex review autopilot loop when a PR you opened receives review activity from Codex (chatgpt-codex-connector[bot]) — triage each finding (act / delegate / escalate / decline-with-reasoning), push fixes, re-ping @codex review, and terminate on approval. Use whenever you see a Codex review, an @Claude wake-up referencing "Codex review autopilot", or are about to handle PR review comments.
---

# Codex review autopilot

The loop that takes a PR from "Codex left a review" to "Codex approved", without
a human babysitting it. Extracted from NordScope's proven implementation; the
server-side wake-up workflows (`wake-on-codex-review`, `wake-on-ci-green`,
`wake-on-ci-red`) are this skill's async counterpart — they re-spawn a session
at each step so the loop survives the session ending.

## Per-repo configuration

This skill is repo-agnostic. Three things are repo-specific; read them from the
repo's `CLAUDE.md` / `.claude/ceremony.config` (or infer from CI):

- **`REQUIRED_CHECKS`** — the check-run names that must be green before re-pinging
  Codex. (NordScope: `typecheck-and-build`, `pytest`, `banned-strings`. Mentra:
  `Backend (pytest)`, `Backend integration (Firestore emulator)`,
  `Frontend (tsc + build)`.)
- **`ESCALATION_HANDLE`** — who the hard-stop pings (default `@WEDDEsign`).
- **`SURFACE_CAPS`** — the rounds-vs-surface caps below; defaults apply if the
  repo doesn't override them.

If the repo has none of these, use the defaults and proceed — do not block on
missing config.

## Standing authorization

When the repo's `CLAUDE.md` grants standing PR authorization, opening the PR,
calling `subscribe_pr_activity` on it, and running this loop are all
pre-approved — do them without asking. **Merging is never auto-approved**:
branch protection + human review remain the sign-off. The cases that escalate
to the user are spelled out under *Escalate* below; everything else is autopilot.

## 1. Act without asking — "mechanical" comments

Push a fix commit, then reply on the thread explaining the change. The
`auto-resolve-review-threads` workflow closes the thread once you (the PR
author / `claude[bot]` / a maintainer identity) reply.

**Mechanical** = lint/type errors, naming, dead code, missing or weak tests,
small local refactors, missing null/empty-state handling, docstring/comment
fixes, formatting, obvious test-coverage gaps, unused imports, swallowed
exceptions, log-level fixes.

## 2. Escalate via `AskUserQuestion` before acting

- **Architectural pushback** — the change touches module boundaries, the data
  model, a public API, or contradicts `CLAUDE.md`.
- **You disagree with Codex** — you think the comment is wrong, a misread, or
  would regress something. *Always escalate disagreement rather than silently
  dropping the comment.*
- **Oscillation** — two consecutive Codex rounds raise conflicting requests on
  the same surface.
- **User-owned decision** — product behavior, copy, pricing, a security tradeoff.

Never silently ignore a Codex finding. Every finding ends in exactly one of:
act, delegate to Codex, escalate, or decline-in-thread-with-reasoning.

## 3. Surface-appropriate scope (rounds-vs-surface cap)

> Lesson learned the expensive way (NordScope PR #325: ~21 rounds, 3600 lines
> for what should have been a 600-line CRUD page). Each finding sounds
> reasonable alone, but the cumulative effect on a low-stakes surface is
> enormous over-engineering. The loop is great at finding *real* edge cases and
> a poor fit for surfaces where those edge cases will never occur.

**Default cap by surface stakes — escalate via `AskUserQuestion` at the cap:**

| Surface | Cap | Why |
|---|---|---|
| Internal admin / CRUD (team + a few superadmins, recoverable by reload) | **3 rounds** | Most defensive edge cases never occur |
| Customer-facing with real traffic (dashboards, report gen, screening, lead-gen) | **8 rounds** | Bigger user base, harder recovery — defensive handling pays off |
| Backend infra / data-model migrations / shared libraries | **15 rounds** | Wide blast radius — worth defending |
| AI surfaces (LLM-call sites, prompt pipelines, eval harnesses) | **15 rounds** | Same blast radius as backend infra |

*This table is self-policed — you decide when you've hit it. §7 below is the
mechanical backstop for when that doesn't happen: it caps at the table's
highest tier (15), not per-surface, so it can trail a low-tier cap by a wide
margin but never a high-tier one.*

**At the cap, the escalation message includes:** round count + approximate diff
size; a one-line summary of each open finding; a recommendation (continue /
prune = squash + strip defensive code / merge as-is).

**Default disposition for low-stakes findings on internal CRUD:**
*"Acknowledged, won't fix — theoretical edge case for our user base."* Reply,
resolve, move on. Examples that default to won't-fix on an admin page: races
between simultaneous superadmin clicks; stale-page failures during pagination;
"what if the API returns malformed data" on endpoints we also wrote;
error-attribution polish; audit granularity beyond succeeded/failed/not-attempted.

**Findings worth fixing even on low-stakes CRUD:** validation gaps that produce
silently-wrong results (misleading 200s on bad input), accessibility blockers,
and data-loss bugs (losing typed work without warning). The bar: *would a real
user silently get the wrong answer or lose data?* When unsure which category a
surface is, ask. The failure mode is silently grinding through every finding.

## 4. Re-review trigger + the label dance

After fix commits land **and** `REQUIRED_CHECKS` are green, post one comment
whose **entire body is the bare phrase `@codex review`** — nothing else.

> **The re-ping must be bare — this is the #1 way the loop breaks.** Codex's
> connector has two modes and infers which from the comment text: `@codex
> review` *alone* → **review mode**; `@codex review` followed by **any** trailing
> prose (a status summary, a commit SHA, "addressed the P2…") → its intent
> classifier reads the prose as a task and flips Codex into **action mode** — it
> writes code, commits, and opens a follow-up PR instead of re-reviewing. No
> review event is emitted, so `wake-on-codex-review` never fires and the
> autopilot silently stalls with no re-ping. (Observed on Mentra PR #919: a
> re-ping reading `@codex review — addressed the P2 … extracting the
> model-compat helpers …` sent Codex off to "restore the fallback" and open a
> follow-up PR; bare `@codex review` comments on the same PR reviewed normally.)
>
> If you want to record what you changed, put it in a **separate** comment
> *before* the trigger — never on the same line — or in the thread replies. Do
> **not** append the "Generated by Claude Code" footer to the trigger comment
> either. Do not nudge while CI is still running.

> **Never write the mention into any OTHER comment either — not even quoted.**
> The connector matches the raw body, so Markdown backticks do not neutralise
> it: a comment that merely *mentions* the trigger while describing next steps
> is still a mention wrapped in prose, i.e. action mode. This is the same bug in
> a second costume, and it bit the wake-up comments themselves — the
> `@Claude` triage wake-up quoted the trigger inside its instruction list, so
> **every autopilot round silently launched a throwaway Codex coding run**
> alongside the review. Fixed in the wake workflows and enforced by
> `scripts/check-codex-mentions.sh`; the same discipline applies to comments
> *you* write. In prose, say "the bare Codex review trigger".
>
> **This includes PR descriptions.** The connector reads the PR body too, so a
> `gh pr create --body` that quotes the handle triggers a coding run at PR-open
> — before you have posted anything. Confirmed on intelligence-core#63: a PR
> whose body quoted the handle got a "Summary / committed `e6202af` / created
> PR metadata" narrative instead of a review, and that commit never landed
> (remote head unchanged). Rewriting the body handle-free and re-pinging bare
> got a normal review on the *same branch*. Commit messages appear to be safe —
> agent-tooling#4 carried a quoted handle in one and still reviewed normally —
> but there is no reason to risk it. The CI guard cannot see PR bodies, so this
> one is on you: **write PR descriptions about this loop without the handle.**

In the async path you usually won't post this at all: `wake-on-ci-green` posts
the bare `@codex review` itself (deterministically, so it can't pick up prose).
You post it by hand only when you're still in-session as CI goes green.

**The session that pushes the fix usually ends before CI finishes**, so don't
poll. Instead, after pushing, do two label operations and let the workflows
take over:

1. **Bump the round counter.** Read the existing `codex-round-N` label (absent ⇒
   `N=0`), remove it, add `codex-round-{N+1}` (create on the fly if missing).
   This counter drives the soft-warn (≥10) and hard-stop (≥15) thresholds —
   skip it and the hard-stop never fires.

   ```sh
   gh label create "codex-round-${NEXT}" --color BFD4F2 \
     --description "Codex review autopilot round counter" 2>/dev/null || true
   gh pr edit "${PR}" --remove-label "codex-round-${PREV}" 2>/dev/null || true
   gh pr edit "${PR}" --add-label "codex-round-${NEXT}"
   ```

2. **Add `awaiting-codex-reping`.** This is the gate the CI-green/red wake-ups
   watch.

Then: `wake-on-ci-green` fires when `REQUIRED_CHECKS` are green for the head SHA
— it posts the bare `@codex review` itself (directly, so the trigger can never
pick up trailing prose) and removes the label.
`wake-on-ci-red` fires on a failing check — it spawns a session to push another
fix and leaves the label on (loop still in flight). If you're still in-session
when CI goes green, you may post the bare `@codex review` (its own comment,
nothing appended — see the action-mode warning above) and skip the label dance —
but still bump the round counter so the hard-stop stays accurate.

## 5. Empty Codex review (no findings)

Codex sometimes submits a review whose body is only its boilerplate header with
**zero inline comments** (under `state: commented` *or* `changes_requested`).
That is a no-op pass. Do not push fixes or hunt for phantom findings. The
`wake-on-codex-review` workflow filters these out, so you usually won't be
spawned at all. If you are invoked anyway, wait for green checks then post one
`@codex review`; don't loop more than once per empty pass before escalating.

## 6. Termination — what counts as "done"

Stop when **any** is true:

- Codex submits a review with state `APPROVED`, OR
- Codex posts a comment/review whose body contains the literal uppercase token
  `APPROVED` on its own line, OR
- The comment is by `chatgpt-codex-connector[bot]` and starts with Codex's fixed
  approval template — case-insensitive `Codex Review: Didn't find any major
  issues` (± trailing 👍). Codex's bot uses this template instead of the
  `APPROVED` token, and it is not configurable from the repo side.

A bare 👍, "lgtm", "looks good", or a green-styled review *without* the
`APPROVED` token or the template do **not** terminate the loop.

## 7. Hard stop (workflow-enforced, layered under §3)

The round counter enforces termination as a backstop under the surface caps in
§3 — those are self-policed (you notice and escalate); this is mechanical
(the workflow acts whether or not you noticed):

- **Soft-warn at round 10** (`softwarn_round` input, repo-tunable) — the
  wake-up workflows append a reminder of §3's caps and ask whether you're
  already past your surface's own number. Don't escalate on every soft-warn
  (a wide-blast-radius surface can legitimately need more than 10 rounds);
  **do** escalate if round N's findings look like round N-2's (oscillation,
  not progress), or if you're already past your tier's cap.
- **Hard-stop at round 15** (`hardstop_round` input, repo-tunable) — the
  workflows refuse to wake Claude, post `ESCALATION_HANDLE` instead, and add
  `loop-stuck`. The autopilot is paused until a human removes `loop-stuck`,
  which re-arms the loop on the next Codex review. 15 matches §3's own
  highest tier (backend infra / data-model migrations / shared libraries /
  AI surfaces) — it is a blunt, surface-blind backstop for when §3's
  self-policing didn't catch it in time, not a per-tier limit itself: a
  3-round-tier CRUD PR can still reach round 15 before this fires.

  This used to be round 25 — above every §3 tier, so it never actually
  engaged. Mentra#2839 (a data-model migration, §3's 15-round tier) ran to
  round 16 before a human intervened manually; the mechanical hard-stop was
  never in the picture. Lowered in agent-tooling#15.

## Delegating back to Codex

For single-file, mechanical, unambiguous fixes you'd rather Codex make, reply
`@codex address that feedback` (or `@codex <task>`) instead of fixing it
yourself — see the `/delegate-to-codex` command. This is **action mode** on
purpose: here you *want* Codex to write code. That's exactly why the re-review
trigger in §4 must stay bare — appending prose to `@codex review` silently turns
a re-review into one of these task hand-offs. Use the `codex-task` /
`claude-task` / `human-task` labels to route issues the same way.
