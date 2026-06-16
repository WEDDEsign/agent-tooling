---
description: Delegate a task to Codex on a PR — formats the @codex comment and applies the codex-task routing rule (single-file, mechanical, unambiguous → Codex; otherwise keep it).
argument-hint: "[PR number] [task, or omit to address current review feedback]"
---

Delegate work to Codex (`chatgpt-codex-connector[bot]`) via a PR comment.

**First apply the routing rule of thumb.** Codex is a good fit only for work
that is **single-file, mechanical, and unambiguous** (the `codex-task` label
criteria): lint/type fixes, naming, dead-code removal, structural cleanup,
mechanical test additions, audits. If the task is multi-file, architectural, or
needs judgment (`claude-task`), do it yourself instead of delegating. If it needs
a product/design/external decision (`human-task`), escalate — don't delegate.

If it passes the rule, post the comment on the target PR:

- To hand off a **specific task**: `@codex <clear, self-contained instruction>`.
- To have Codex **act on its own review feedback**: `@codex address that feedback`.

`@codex` (and `@codex review`) are the only trigger phrases Codex's bot
documents — `@chatgpt-codex-connector` or plain English won't fire it.

Keep the instruction tight and bounded: name the file(s), the exact change, and
the acceptance check. Codex is stateless — it re-reads the PR each time, so don't
rely on it remembering earlier context.

Arguments: `$1` = PR number (default: the PR for the current branch), `$ARGUMENTS`
after the number = the task (omit to mean "address that feedback").
