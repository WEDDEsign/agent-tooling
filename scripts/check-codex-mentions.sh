#!/usr/bin/env bash
# Guard: a Codex mention must never appear in a comment body except as the
# bare review trigger.
#
# Codex's GitHub connector matches its mention handle in the RAW body of any PR
# comment from a permitted user — Markdown backticks are invisible to it. So a
# wake-up comment that merely *quotes* the trigger while telling Claude to
# "push fix commits" reads to Codex as a task, and it flips into action mode:
# writes code, commits, claims a follow-up PR, and never submits a review — so
# no `pull_request_review` event fires and the autopilot loop stalls. Every
# round then burns Codex credits on work that is thrown away.
#
# This has now been re-broken twice in different forms (trailing prose on the
# trigger itself, then quoting the trigger inside an unrelated comment), hence
# a mechanical check rather than another doc note.
#
# Rule: after stripping comment lines (YAML `#` and shell `#` never reach a
# comment body), the mention may appear ONLY on the exact bare-trigger line.
# In prose, write "the bare Codex review trigger" instead.
#
# Detection is case-insensitive because GitHub resolves mentions that way, so a
# capitalised variant in prose is the same live mention and the same bug. The
# allowlist stays case-SENSITIVE: only the canonical lower-case trigger is
# permitted, so a non-canonical spelling of the trigger itself is also flagged.

set -euo pipefail

# Assembled at runtime so this script does not itself contain the literal
# mention — otherwise the guard would flag its own source.
HANDLE="@""codex"
BARE_TRIGGER_LINE='--body "'"${HANDLE}"' review"'

status=0
scanned=0

while IFS= read -r file; do
  scanned=$((scanned + 1))
  # Drop whole-line comments; they are documentation, not posted text.
  offenders=$(sed 's/^[[:space:]]*#.*$//' "$file" \
    | grep -n -i -F -e "${HANDLE}" \
    | grep -v -F -e "${BARE_TRIGGER_LINE}" \
    || true)

  if [ -n "$offenders" ]; then
    status=1
    echo "::error file=${file}::Codex mention in a comment body outside the bare review trigger"
    echo "${file}:"
    echo "${offenders}" | sed 's/^/  /'
    echo
  fi
done < <(find .github/workflows templates -name '*.yml' -type f)

# A guard that silently scans nothing is worse than no guard — it reports green
# forever. Fail loudly if the file discovery came back empty.
if [ "$scanned" -eq 0 ]; then
  echo "::error::check-codex-mentions scanned 0 files — file discovery is broken."
  exit 1
fi

if [ "$status" -ne 0 ]; then
  cat <<EOF
A Codex mention outside the bare review trigger will flip Codex into action
mode: it writes code instead of reviewing, no review event fires, and the
autopilot loop stalls while burning credits.

In prose, write "the bare Codex review trigger" — never the mention itself.
The only permitted occurrence is the trigger line in wake-on-ci-green.yml.
EOF
  exit 1
fi

echo "OK: no Codex mentions outside the bare review trigger."
