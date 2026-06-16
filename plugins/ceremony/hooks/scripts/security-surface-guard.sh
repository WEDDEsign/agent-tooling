#!/usr/bin/env sh
#
# security-surface-guard.sh — PreToolUse guard for the ceremony plugin.
#
# Fires before Edit/Write/MultiEdit. If the target file is on a security-
# sensitive surface (auth, tenancy, secrets, routing manifests, security
# config), it forces a confirmation prompt ("ask") so the edit can't slip
# through unnoticed in an autopilot session. Everything else is allowed
# silently.
#
# This is a CLIENT-SIDE guard: it only fires in an interactive Claude Code
# session. It does NOT protect web/cloud sessions or Codex's own commits —
# pair it with a server-side backstop (CODEOWNERS / required review on the
# same paths). See docs/per-repo-wiring.md.
#
# Dependency-free: POSIX sh + grep + sed (no jq), so it runs under Git Bash
# on Windows and bash on Linux/macOS identically.
#
# Extend per repo by exporting CEREMONY_GUARD_EXTRA_REGEX with an extended
# regex (ERE) of additional paths to guard.
set -u

input=$(cat)

# Extract tool_input.file_path from the PreToolUse JSON without a JSON parser.
# Matches "file_path": "..." and unescapes \/ -> /.
file_path=$(printf '%s' "$input" \
  | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' \
  | head -n1 \
  | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//; s/"$//; s#\\/#/#g')

# Nothing to guard (e.g. tool without a file_path) -> allow.
if [ -z "$file_path" ]; then
  exit 0
fi

# Default sensitive-surface patterns, case-insensitive. Generalized across the
# stack: authn/z, tenancy scoping, secrets/credentials, routing/api manifests,
# security config, and environment files.
DEFAULT_REGEX='(^|/)(\.env)|(/|_|-)?(auth|authn|authz)([/._-]|$)|tenan(t|cy)|secret|credential|(^|/)token([/._-]|$)|routers?\.json|api[_-]?manifest|(^|/)security([/._-]|$)|middleware/.*auth|service[_-]?account|private[_-]?key|firestore\.rules|\.pem$|id_rsa'

extra="${CEREMONY_GUARD_EXTRA_REGEX:-}"

matched=$(printf '%s' "$file_path" | grep -Eic "$DEFAULT_REGEX" || true)
if [ -n "$extra" ]; then
  extra_matched=$(printf '%s' "$file_path" | grep -Eic "$extra" || true)
else
  extra_matched=0
fi

if [ "$matched" = "0" ] && [ "$extra_matched" = "0" ]; then
  exit 0
fi

# Sensitive surface -> ask for explicit confirmation. The reason is shown to
# the user in the permission prompt.
reason="Security-surface guard: \`$file_path\` looks like a sensitive surface (auth / tenancy / secrets / routing / security config). Confirm this edit is intended, correctly tenant-scoped, and leaks no secret before approving."

# Emit the PreToolUse decision JSON. permissionDecision=ask forces a prompt.
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$reason"
exit 0
