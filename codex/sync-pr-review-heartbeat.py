#!/usr/bin/env python3
"""Sync the shared Codex PR-heartbeat procedure into a repo's AGENTS.md."""

from pathlib import Path
import sys


START = (
    "<!-- weddesign-codex-pr-heartbeat:start — synced from "
    "agent-tooling/codex/pr-review-heartbeat/SKILL.md; edit there, then re-sync -->"
)
END = "<!-- weddesign-codex-pr-heartbeat:end -->"
SOURCE = Path(__file__).parent / "pr-review-heartbeat" / "SKILL.md"


def render_section() -> str:
    skill = SOURCE.read_text(encoding="utf-8")
    _, _, remainder = skill.partition("---\n")
    _, separator, procedure = remainder.partition("---\n")
    if not separator:
        raise ValueError(f"missing YAML frontmatter in {SOURCE}")
    procedure = procedure.strip()
    return f"{START}\n\n{procedure}\n\n{END}\n"


def sync(target: Path) -> str:
    section = render_section()
    existing = target.read_text(encoding="utf-8") if target.exists() else ""

    if START in existing and END in existing:
        before, remainder = existing.split(START, 1)
        _, after = remainder.split(END, 1)
        updated = before.rstrip("\n") + "\n\n" + section + after.lstrip("\n")
        action = "updated"
    elif existing.strip():
        updated = existing.rstrip("\n") + "\n\n" + section
        action = "appended to"
    else:
        updated = section
        action = "created"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(updated, encoding="utf-8", newline="\n")
    return action


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sync-pr-review-heartbeat.py <path-to-AGENTS.md>", file=sys.stderr)
        return 2

    target = Path(sys.argv[1])
    action = sync(target)
    print(f"Codex PR heartbeat {action}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
