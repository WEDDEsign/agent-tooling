# WEDDEsign code & architecture doctrine

> The standard Codex and Claude hold every change to — for quality,
> maintainability, and scalability. **Short on purpose:** a doctrine nobody
> reads isn't enforced. The universal rules live here; each repo's `AGENTS.md`
> adds its own concrete patterns (Mentra's `<Page>`/`<Section>` + tenancy,
> VectraIQ's enrichment lifecycle, intelligence-core's contracts-first rule).
> The mechanical parts are enforced by the `file-size-ratchet`; the rest is
> applied in Codex review and by Claude while writing.

## 1. File size is a smell, not a number

- New code files target **< 500 lines**. Files over **~400 lines** are "large".
- **Existing large files are grandfathered — but must not grow.** You pay down
  size by *splitting*, never by adding to a file that's already big. The
  `file-size-ratchet` fails CI/preflight when a file over the soft limit gains
  lines, or a new file exceeds the hard limit.
- Size only flags a likely problem. A 450-line file doing **one** thing is fine;
  a 250-line file doing **three** is not. Split on **responsibility**, not on a
  line count.

## 2. One reason to change

- A file, module, or function should have a single responsibility. If you need
  "and" to describe what it does, it's two things — split them.
- **Thin edges, fat core.** Routers / handlers / components stay thin: parse,
  delegate, render. Real logic lives one layer in (Mentra `app/apis/*` thin →
  `app/libs/*` logic; same shape in VectraIQ and core). A router with business
  logic in it is a bug waiting to be duplicated.

## 3. Prefer the existing pattern over a new abstraction

- Before inventing, find how this codebase already solves the problem and match
  it. A novel pattern for a solved problem is debt dressed as cleverness, and it
  multiplies the shapes the next person has to learn.
- **Rule of three:** don't abstract on the first repeat — abstract on the third.
  Premature abstraction costs as much as duplication and is harder to undo.

## 4. No new duplication — but don't couple by coincidence

- Don't copy logic across modules. If it's genuinely shared, lift it to the
  right layer (a shared lib; or `intelligence-core` if it's cross-product
  contract/behavior).
- **Shared-by-coincidence ≠ shared-by-meaning.** Two blocks that look alike
  today but change for different reasons should stay separate. Wrong coupling is
  more expensive than a little duplication.

## 5. Dependencies point one way; size the change to its blast radius

- Dependencies flow inward/downward: domain/core never imports features; shared
  contracts (`intelligence-core`) never import a product. A cycle or an upward
  import is an architecture break, not a style nit.
- **Match the surface's stakes** (the same scale `codex-review-triage` uses):
  internal/CRUD tools — keep it minimal, skip speculative hardening;
  customer-facing & shared-infra/migrations — defend, because the blast radius
  is real. Over-engineering a low-stakes surface and under-defending a
  high-blast-radius one are the *same* mistake.

## 6. Complexity: flat, named, shallow

- Prefer flat over nested and early-return over deep `if`/`else`. A function
  more than ~3 levels deep, or doing more than one job, wants splitting.
- **Name for intent.** A good name deletes the comment that would have explained
  it. If you can't name it clearly, you don't yet understand what it does.

## 7. Tests track behavior, not lines

- New behavior gets a test; a fixed bug gets a regression test. Don't pad
  coverage with tests that re-assert the implementation — they lock in the
  code's shape and make refactoring expensive.

---

### How this is enforced (three-way, same as the ceremony)

- **CI + `preflight`** run the `file-size-ratchet` — mechanical, automatic, local-first.
- **Codex** applies this doctrine on every review (it lives in each repo's `AGENTS.md`).
- **Claude** follows it while writing (the `code-doctrine` skill).

### Tuning

Thresholds are deliberately conservative defaults. Adjust per repo via
`DOCTRINE_SOFT_LIMIT` / `DOCTRINE_HARD_LIMIT` (and `DOCTRINE_EXTS`) — but move
the ratchet in the **don't-get-worse** direction, never loosen it to dodge a
split.
