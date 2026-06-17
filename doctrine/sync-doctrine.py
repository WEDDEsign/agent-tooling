#!/usr/bin/env python3
"""
sync-doctrine — write the shared CODE_DOCTRINE.md into a repo's AGENTS.md as a
marked, re-syncable section, so Codex (which reads AGENTS.md) enforces it.

Idempotent: replaces the content between the doctrine markers if present, or
appends the section if not. Everything outside the markers — the repo's own
patterns and deltas — is left untouched.

Usage:  sync-doctrine.py <path-to-AGENTS.md>
        (creates the file with just the doctrine section if it doesn't exist)
"""
import os
import sys

START = "<!-- weddesign-doctrine:start — synced from agent-tooling/doctrine/CODE_DOCTRINE.md; edit there, then re-sync -->"
END = "<!-- weddesign-doctrine:end -->"

here = os.path.dirname(os.path.abspath(__file__))
doctrine_path = os.path.join(here, "CODE_DOCTRINE.md")


def main():
    if len(sys.argv) != 2:
        print("usage: sync-doctrine.py <path-to-AGENTS.md>", file=sys.stderr)
        return 2
    target = sys.argv[1]
    doctrine = open(doctrine_path, encoding="utf-8").read().strip()
    section = f"{START}\n\n{doctrine}\n\n{END}\n"

    if os.path.exists(target):
        text = open(target, encoding="utf-8").read()
    else:
        text = ""

    if START in text and END in text:
        pre = text.split(START)[0]
        post = text.split(END, 1)[1]
        new = pre.rstrip("\n") + "\n\n" + section + post.lstrip("\n")
        action = "updated"
    elif text.strip():
        new = text.rstrip("\n") + "\n\n" + section
        action = "appended to"
    else:
        new = section
        action = "created"

    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    print(f"doctrine {action}: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
