#!/usr/bin/env python3
"""
file-size-ratchet — the "don't get worse" file-size gate from CODE_DOCTRINE.md.

For each code file added/modified vs the base ref:
  - NEW file:  fail if it exceeds the HARD limit. Start small; split before you ship a giant.
  - EXISTING file already over the SOFT limit: fail if it GREW (more lines than at base).
    Large files are grandfathered — you pay down size by splitting, never by adding to them.
  - Everything else passes.

This is intentionally blunt and mechanical: it never argues about whether a file
*should* be split — it just stops the codebase getting worse. Judgment about
*how* to split is the doctrine's + Codex's job.

Usage:   file-size-ratchet.py [base-ref]          # default base: origin/main
Env:     DOCTRINE_SOFT_LIMIT  (default 400)
         DOCTRINE_HARD_LIMIT  (default 500)
         DOCTRINE_EXTS        (default ".py,.ts,.tsx,.js,.jsx")
         DOCTRINE_EXCLUDE     (comma-separated path substrings to skip, e.g. "migrations/,generated/")
Exit:    non-zero on any violation.
"""
import os
import subprocess
import sys

SOFT = int(os.environ.get("DOCTRINE_SOFT_LIMIT", "400"))
HARD = int(os.environ.get("DOCTRINE_HARD_LIMIT", "500"))
EXTS = tuple(e.strip() for e in os.environ.get("DOCTRINE_EXTS", ".py,.ts,.tsx,.js,.jsx").split(",") if e.strip())
EXCLUDE = [s.strip() for s in os.environ.get("DOCTRINE_EXCLUDE", "").split(",") if s.strip()]
BASE = sys.argv[1] if len(sys.argv) > 1 else "origin/main"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def lines_at(ref, path):
    """Line count of path at a git ref, or None if it didn't exist there."""
    out = git("show", f"{ref}:{path}")
    return None if out.returncode != 0 else len(out.stdout.splitlines())


def lines_now(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)
    except FileNotFoundError:
        return None


def main():
    mb = git("merge-base", BASE, "HEAD").stdout.strip() or BASE
    changed = git("diff", "--name-only", "--diff-filter=AM", f"{mb}...HEAD").stdout.split()
    files = [
        f for f in changed
        if f.endswith(EXTS) and not any(x in f for x in EXCLUDE)
    ]

    violations = []
    for f in files:
        cur = lines_now(f)
        if cur is None:
            continue
        was = lines_at(mb, f)
        if was is None:  # new file
            if cur > HARD:
                violations.append(
                    f"NEW  {f}: {cur} lines (> hard limit {HARD}). Split it before shipping."
                )
        elif cur > SOFT and cur > was:  # already-large file that grew
            violations.append(
                f"GREW {f}: {was} → {cur} lines (over soft limit {SOFT}). "
                f"This file is already large — don't grow it, split it."
            )

    if violations:
        print("file-size ratchet — violations:\n")
        for v in violations:
            print("  ✗ " + v)
        print(
            f"\nDoctrine: new code files < {HARD} lines; files over {SOFT} lines must not grow.\n"
            f"Fix by splitting on responsibility (see CODE_DOCTRINE.md §1–2). "
            f"Tune via DOCTRINE_SOFT_LIMIT / DOCTRINE_HARD_LIMIT only in the stricter direction."
        )
        return 1

    print(f"file-size ratchet: ok ({len(files)} code file(s) checked; soft={SOFT}, hard={HARD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
