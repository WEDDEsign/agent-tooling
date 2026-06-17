#!/usr/bin/env python3
"""file-size-ratchet — the "don't get worse" file-size gate from CODE_DOCTRINE.md.

For each code file added / modified / renamed vs the base ref:
  - NEW file:  fail if it exceeds the HARD limit. Start small; split before shipping a giant.
  - EXISTING file already over the SOFT limit: fail if it GREW (more lines than at base).
    Large files are grandfathered — pay down size by splitting, never by adding to them.
  - RENAMED file: compared against its size at the OLD path, so a pure move isn't
    penalized, but moving AND growing a large file is caught.

Fails CLOSED: if the base ref can't be resolved or git fails, it errors rather than
silently passing — a gate that can't see the diff must not green-light it.

Usage:   file-size-ratchet.py [base-ref]          # default base: origin/main
Env:     DOCTRINE_SOFT_LIMIT  (default 400)
         DOCTRINE_HARD_LIMIT  (default 500)
         DOCTRINE_EXTS        (default ".py,.ts,.tsx,.js,.jsx")
         DOCTRINE_EXCLUDE     (comma-separated path substrings to skip)
Exit:    non-zero on any violation, or if it cannot evaluate the diff.
"""

import os
import subprocess
import sys

SOFT = int(os.environ.get("DOCTRINE_SOFT_LIMIT", "400"))
HARD = int(os.environ.get("DOCTRINE_HARD_LIMIT", "500"))
EXTS = tuple(
    e.strip()
    for e in os.environ.get("DOCTRINE_EXTS", ".py,.ts,.tsx,.js,.jsx").split(",")
    if e.strip()
)
EXCLUDE = [s.strip() for s in os.environ.get("DOCTRINE_EXCLUDE", "").split(",") if s.strip()]
BASE = sys.argv[1] if len(sys.argv) > 1 else "origin/main"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def die_closed(msg):
    print(f"file-size ratchet: {msg}")
    print("Failing closed — the gate must not pass a diff it cannot see.")
    return 1


def lines_at(ref, path):
    out = git("show", f"{ref}:{path}")
    return None if out.returncode != 0 else len(out.stdout.splitlines())


def lines_now(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)
    except FileNotFoundError:
        return None


def considered(path):
    return path.endswith(EXTS) and not any(x in path for x in EXCLUDE)


def main():
    mbr = git("merge-base", BASE, "HEAD")
    if mbr.returncode != 0 or not mbr.stdout.strip():
        return die_closed(
            f"cannot resolve base ref '{BASE}' (git merge-base failed). "
            f"Fetch it or pass a valid base."
        )
    mb = mbr.stdout.strip()

    # --name-status with rename detection: A<TAB>path / M<TAB>path / R<score><TAB>old<TAB>new
    dr = git("diff", "--name-status", "--diff-filter=AMR", "-M", f"{mb}...HEAD")
    if dr.returncode != 0:
        return die_closed("git diff failed.")

    # (current_path, base_path_or_None) work items
    items = []
    for line in dr.stdout.splitlines():
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            old, new = parts[1], parts[2]
            if considered(new):
                items.append((new, old))  # compare against the old path's size
        elif status in ("A", "M") and len(parts) >= 2:
            path = parts[1]
            if considered(path):
                items.append((path, None if status == "A" else path))

    violations = []
    for path, base_path in items:
        cur = lines_now(path)
        if cur is None:
            continue
        was = lines_at(mb, base_path) if base_path else None
        if was is None:  # new (or renamed-from-nonexistent)
            if cur > HARD:
                violations.append(
                    f"NEW  {path}: {cur} lines (> hard limit {HARD}). Split it before shipping."
                )
        elif cur > SOFT and cur > was:
            violations.append(
                f"GREW {path}: {was} -> {cur} lines (over soft limit {SOFT}). "
                f"Already large — don't grow it, split it."
            )

    if violations:
        print("file-size ratchet — violations:\n")
        for v in violations:
            print("  x " + v)
        print(
            f"\nDoctrine: new code files < {HARD} lines; files over {SOFT} lines must "
            f"not grow.\nFix by splitting on responsibility (CODE_DOCTRINE.md). "
            f"Tune DOCTRINE_SOFT_LIMIT / DOCTRINE_HARD_LIMIT only stricter."
        )
        return 1

    print(f"file-size ratchet: ok ({len(items)} code file(s) checked; soft={SOFT}, hard={HARD})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
