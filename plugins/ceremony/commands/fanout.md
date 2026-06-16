---
description: Spawn N subagents for declared-parallel work — non-overlapping batches (migrations, multi-file sweeps) or independent perspectives (adversarial review). Only for work that is genuinely independent.
argument-hint: "[describe the parallel work and the batches]"
---

Fan work out across subagents — but only when it is **genuinely independent**.

**Gate first.** Fan-out is correct only when the units of work don't touch each
other. Two valid shapes:

- **Disjoint batches** — e.g. a migration applied to N non-overlapping
  files/modules, an audit split by directory. Each subagent owns its batch; no
  two write the same file. If batches might conflict, give each its own git
  worktree (`isolation: "worktree"`).
- **Independent perspectives** — N subagents reviewing the *same* change through
  *different* lenses (correctness / security / performance / does-it-reproduce),
  then synthesize. This is the adversarial-review pattern: diverse lenses catch
  what one pass misses.

**Do NOT fan out** sequential work (each step needs the previous result),
anything where the batches share mutable state, or work small enough that one
agent is faster than the coordination overhead.

To run it:

1. **Declare the split explicitly** — list the batches/lenses and what each
   subagent owns. Put the split in writing before spawning.
2. **Spawn all subagents in a single message** (multiple Agent calls) so they run
   concurrently. Give each a tightly-scoped, self-contained prompt and, for write
   work, worktree isolation.
3. **Collect and reconcile** — merge results, dedupe, and for review fan-outs
   keep only findings ≥majority of lenses confirm.

For large, structured fan-out (dozens of items, multi-stage verify), prefer a
Workflow over ad-hoc Agent calls — it pipelines and caps concurrency for you.

`$ARGUMENTS` = the work to parallelize and the proposed batching.
