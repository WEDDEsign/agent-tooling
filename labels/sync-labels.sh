#!/usr/bin/env bash
#
# sync-labels.sh — apply the canonical label taxonomy (labels/labels.json) to a repo.
#
# Usage:
#   labels/sync-labels.sh WEDDEsign/Mentra            # apply to one repo
#   labels/sync-labels.sh WEDDEsign/VectraIQ --dry-run
#   for r in Mentra VectraIQ intelligence-core NordScope; do \
#     labels/sync-labels.sh "WEDDEsign/$r"; done       # apply to all four
#
# Idempotent: `gh label create --force` creates the label or updates its color
# and description if it already exists. Existing labels not in the manifest are
# left untouched (this is additive, never destructive — it will not delete the
# `codex` legacy label or any repo-local labels).
#
# Requires: gh (authenticated), jq.
set -euo pipefail

REPO="${1:-}"
DRY_RUN="false"
[ "${2:-}" = "--dry-run" ] && DRY_RUN="true"

if [ -z "$REPO" ]; then
  echo "usage: $0 <owner/repo> [--dry-run]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/labels.json"

command -v gh >/dev/null || { echo "gh not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

echo "Syncing canonical labels to $REPO (dry-run=$DRY_RUN)"

jq -c '.labels[]' "$MANIFEST" | while read -r row; do
  name=$(echo "$row"  | jq -r '.name')
  color=$(echo "$row" | jq -r '.color')
  desc=$(echo "$row"  | jq -r '.description')
  # GitHub rejects label descriptions >100 chars with a 422; fail loudly here
  # rather than silently leaving the old description in place.
  if [ "${#desc}" -gt 100 ]; then
    echo "ERROR: description for '$name' is ${#desc} chars (>100). Shorten it in labels.json." >&2
    exit 1
  fi
  echo "  • $name (#$color) — $desc"
  if [ "$DRY_RUN" = "true" ]; then
    continue
  fi
  gh label create "$name" \
    --repo "$REPO" \
    --color "$color" \
    --description "$desc" \
    --force
done

echo "Done. Note: codex-round-<N> labels are created on the fly by the autopilot and are not seeded here."
