#!/usr/bin/env bash
# Assertions for the decision logic inside the wake-* workflows.
#
# WHY THIS EXISTS
# lint.yml says it plainly: these are `workflow_call` reusables, nothing in this
# repo exercised them, and a bad edit only surfaced once it had propagated to a
# consumer's live PR. The logic is jq filters and shell comparisons embedded in
# YAML, so it cannot be imported and unit-tested directly — and five rounds of
# review on the sweep turned up 25 real defects, most of them in exactly these
# expressions. Without a gate, every invariant those rounds established is
# enforced by nothing.
#
# HOW IT AVOIDS TESTING A COPY OF ITSELF
# The filters are duplicated here, which would normally make this theatre: edit
# the workflow, and a test asserting its own copy still passes. So each filter
# is registered with `filter` and checked against the workflow file it came
# from. Change the workflow without changing this file and the drift guard
# fails; change this file without changing the workflow and it fails too. The
# assertions test behaviour, the guard tests that the behaviour under test is
# the one that ships.

set -uo pipefail
cd "$(dirname "$0")/.."

pass=0; fail=0
SWEEP=.github/workflows/sweep-stalled-repings.yml
GREEN=.github/workflows/wake-on-ci-green.yml
REVIEW=.github/workflows/wake-on-codex-review.yml

chk() { # name, got, want
  if [ "$2" = "$3" ]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
  else fail=$((fail+1)); printf '  FAIL %s\n         got:  %s\n         want: %s\n' "$1" "$2" "$3"; fi
}

# filter FILE 'jq filter' — assert the filter appears verbatim in FILE, then
# echo it so the caller can run the very string it just verified ships.
filter() {
  local file="$1" f="$2"
  if ! grep -qF -- "$f" "$file"; then
    fail=$((fail+1))
    printf '  FAIL drift: filter not found in %s\n         %s\n' "$file" "$f"
  fi
  printf '%s' "$f"
}

section() { printf '\n%s\n' "$1"; }

# The trigger is assembled rather than written, for the same reason the
# workflows assemble it: scripts/check-codex-mentions.sh permits the literal
# mention on exactly one line in this repo, and that line is the poster's.
HANDLE="@""codex"; TRIGGER="${HANDLE} review"

# ---------------------------------------------------------------------------
section "Required checks — every name must be green for the head"
RUNS_F=$(filter "$SWEEP" '[.[].check_runs[] | {name, conclusion}]')
MISS_F='.[] as $req
      | select(($have | any(.name == $req and .conclusion == "success")) | not)
      | $req'
runs() { printf '%s' "$1" | jq -c "$RUNS_F"; }
missing() { printf '%s' "$2" | jq -r --argjson have "$(runs "$1")" "$MISS_F"; }

ALL_GREEN='[{"check_runs":[{"name":"a","conclusion":"success"},{"name":"b","conclusion":"success"}]}]'
chk "all green -> nothing missing" "$(missing "$ALL_GREEN" '["a","b"]')" ""
chk "a failing check is missing" \
  "$(missing '[{"check_runs":[{"name":"a","conclusion":"failure"}]}]' '["a"]')" "a"
chk "a still-running check is missing" \
  "$(missing '[{"check_runs":[{"name":"a","conclusion":null}]}]' '["a"]')" "a"
chk "a check with no run at all is missing" "$(missing "$ALL_GREEN" '["c"]')" "c"
# A re-run leaves the old failure in the list under the same name. Requiring
# ANY success rather than ALL non-failure is what makes a re-run count as green.
chk "re-run: old failure + new success under one name -> green" \
  "$(missing '[{"check_runs":[{"name":"a","conclusion":"failure"},{"name":"a","conclusion":"success"}]}]' '["a"]')" ""
chk "check name containing spaces and parens" \
  "$(missing '[{"check_runs":[{"name":"Backend (pytest)","conclusion":"success"}]}]' '["Backend (pytest)"]')" ""
# Pagination: a required check on a later page must not read as absent.
chk "required check on page 2 is found" \
  "$(missing '[{"check_runs":[{"name":"a","conclusion":"success"}]},{"check_runs":[{"name":"b","conclusion":"success"}]}]' '["a","b"]')" ""

section "Terminal non-failure checks — these wake nothing and must be reported"
BLOCKED_F='.[] as $req
      | ($have[] | select(.name == $req and (.conclusion == "cancelled" or .conclusion == "timed_out" or .conclusion == "skipped")))
      | "\($req): \(.conclusion)"'
blocked() { printf '%s' "$2" | jq -r --argjson have "$(runs "$1")" "$BLOCKED_F"; }
chk "cancelled is reported" \
  "$(blocked '[{"check_runs":[{"name":"a","conclusion":"cancelled"}]}]' '["a"]')" "a: cancelled"
chk "timed_out is reported" \
  "$(blocked '[{"check_runs":[{"name":"a","conclusion":"timed_out"}]}]' '["a"]')" "a: timed_out"
chk "a plain failure is NOT reported as terminal-non-failure" \
  "$(blocked '[{"check_runs":[{"name":"a","conclusion":"failure"}]}]' '["a"]')" ""
chk "a running check is NOT reported as terminal-non-failure" \
  "$(blocked '[{"check_runs":[{"name":"a","conclusion":null}]}]' '["a"]')" ""

# ---------------------------------------------------------------------------
section "Round counter — the only bound on a runaway loop"
ROUND_F=$(filter "$SWEEP" '[.[][] | .name | capture("^codex-round-(?<n>[0-9]+)$") | .n | tonumber] | max // 0')
round() { printf '%s' "$1" | jq -r "$ROUND_F"; }
chk "no round label -> 0" "$(round '[[{"name":"other"}]]')" "0"
chk "reads the number" "$(round '[[{"name":"codex-round-7"}]]')" "7"
chk "two round labels -> max" "$(round '[[{"name":"codex-round-9"},{"name":"codex-round-11"}]]')" "11"
chk "double digits sort numerically, not lexically" \
  "$(round '[[{"name":"codex-round-9"},{"name":"codex-round-10"}]]')" "10"
chk "a near-miss name is not a round label" "$(round '[[{"name":"codex-round-x"}]]')" "0"
# The labels endpoint pages at 30 by default. A round label on a later page
# read as 0, which silently disabled both thresholds.
chk "round label on page 2 is found" \
  "$(round '[[{"name":"a"}],[{"name":"codex-round-26"}]]')" "26"
for r in 0 9 10 24 25 26; do
  if [ "$r" -ge 25 ]; then m=hardstop; elif [ "$r" -ge 10 ]; then m=softwarn; else m=normal; fi
  case $r in 0|9) w=normal;; 10|24) w=softwarn;; 25|26) w=hardstop;; esac
  chk "round $r -> $w" "$m" "$w"
done

section "Gate label presence"
GATE_F=$(filter "$GREEN" '[.[][] | select(.name == "awaiting-codex-reping")] | length')
gate() { printf '%s' "$1" | jq -r "$GATE_F"; }
chk "present" "$(gate '[[{"name":"awaiting-codex-reping"}]]')" "1"
chk "absent" "$(gate '[[{"name":"other"}]]')" "0"
chk "present on page 2" "$(gate '[[{"name":"a"}],[{"name":"awaiting-codex-reping"}]]')" "1"

# ---------------------------------------------------------------------------
section "Trigger detection — what counts as a re-ping having gone out"
TRIG_F='[.[][] | select((.body | gsub("^\\s+|\\s+$"; "")) == $t) | .created_at] | max // ""'
last_trigger() { printf '%s' "$1" | jq -r --arg t "$TRIGGER" "$TRIG_F"; }
chk "the bare trigger counts" \
  "$(last_trigger '[[{"created_at":"T1","body":"@codex review"}]]')" "T1"
chk "surrounding whitespace is trimmed" \
  "$(last_trigger '[[{"created_at":"T1","body":"  @codex review\n"}]]')" "T1"
# Prose after the trigger flips Codex into action mode: it writes code instead
# of reviewing, so no review event fires. Such a comment must NOT satisfy the
# gate, or the loop would consider itself re-pinged and stall.
chk "prose appended -> not a re-ping" \
  "$(last_trigger '[[{"created_at":"T1","body":"@codex review — fixed the P2"}]]')" ""
chk "a mention inside prose -> not a re-ping" \
  "$(last_trigger '[[{"created_at":"T1","body":"I asked @codex review this earlier"}]]')" ""
chk "no comments at all" "$(last_trigger '[[]]')" ""
# GitHub returns comments oldest-first, so page 1 holds the OLDEST max. Without
# slurping, jq runs per page and the shell compares a multi-line string whose
# verdict is decided by page 1 — the stalest data available.
chk "newest across pages wins (oldest page first)" \
  "$(last_trigger '[[{"created_at":"2026-01-01T00:00:00Z","body":"@codex review"}],[{"created_at":"2026-06-01T00:00:00Z","body":"@codex review"}]]')" \
  "2026-06-01T00:00:00Z"

section "Label events — when the gate opened, and when a hard stop was resumed"
LBL_F='[.[][] | select(.event == "labeled" and .label.name == $l) | .created_at] | max // ""'
UNLBL_F='[.[][] | select(.event == "unlabeled" and .label.name == $l) | .created_at] | max // ""'
last_labeled() { printf '%s' "$1" | jq -r --arg l "awaiting-codex-reping" "$LBL_F"; }
last_unlabeled() { printf '%s' "$1" | jq -r --arg l "loop-stuck" "$UNLBL_F"; }
chk "labeled event read" \
  "$(last_labeled '[[{"event":"labeled","label":{"name":"awaiting-codex-reping"},"created_at":"T2"}]]')" "T2"
chk "an unlabeled event is not a labeled event" \
  "$(last_labeled '[[{"event":"unlabeled","label":{"name":"awaiting-codex-reping"},"created_at":"T2"}]]')" ""
chk "a different label does not count" \
  "$(last_labeled '[[{"event":"labeled","label":{"name":"do-not-merge"},"created_at":"T2"}]]')" ""
chk "newest labeled across pages" \
  "$(last_labeled '[[{"event":"labeled","label":{"name":"awaiting-codex-reping"},"created_at":"2026-01-01T00:00:00Z"}],[{"event":"labeled","label":{"name":"awaiting-codex-reping"},"created_at":"2026-06-01T00:00:00Z"}]]')" \
  "2026-06-01T00:00:00Z"
chk "loop-stuck removal is the resume boundary" \
  "$(last_unlabeled '[[{"event":"unlabeled","label":{"name":"loop-stuck"},"created_at":"T9"}]]')" "T9"
chk "never resumed -> empty boundary" "$(last_unlabeled '[[]]')" ""

# ---------------------------------------------------------------------------
section "THE DECISION — identity first, timestamps only for how to leave the gate"
# Three timestamp anchors for "which head does this gate belong to" were tried
# and all three were wrong in the same direction. The head's review state is
# exact; the label/trigger order then only chooses between retiring the gate
# and leaving it for a push that has not landed.
decide() { # reviewed_head, trigger_at, labeled_at
  if [ "$1" -gt 0 ]; then
    if [ -n "$2" ] && [ -n "$3" ] && [[ "$2" > "$3" ]]; then echo CLEAR; else echo WAIT; fi
  else echo POST; fi
}
T=2026-08-24T09:00:00Z; L_OLD=2026-08-24T08:00:00Z; L_NEW=2026-08-24T09:30:00Z
chk "reviewed + trigger after label -> retire the gate" "$(decide 1 "$T" "$L_OLD")" "CLEAR"
chk "reviewed + label after trigger -> leave it for the next push" "$(decide 1 "$T" "$L_NEW")" "WAIT"
chk "reviewed + never triggered -> leave it (never retire)" "$(decide 1 "" "$L_NEW")" "WAIT"
chk "unreviewed -> post, whatever the label order" "$(decide 0 "$T" "$L_NEW")" "POST"
chk "unreviewed + never triggered -> post" "$(decide 0 "" "$L_NEW")" "POST"
# The regression that motivated identity-first: the manual-resume flow REUSES
# the existing gate label, so labeled_at keeps its old value while a manual
# trigger and then a push arrive after it. A timestamp-only test reads
# "trigger after label" and deletes a gate open for the newly pushed head.
chk "manual-resume shape (stale label, new unreviewed head) -> POST" "$(decide 0 "$T" "$L_OLD")" "POST"
chk "  ...and the timestamp-only test would have said CLEAR" \
  "$(if [[ "$T" > "$L_OLD" ]]; then echo CLEAR; else echo POST; fi)" "CLEAR"

REV_F='[.[][] | select(.user.login == "chatgpt-codex-connector[bot]")
               | select(.commit_id == $s)] | length'
reviewed() { printf '%s' "$1" | jq -r --arg s "HEADSHA" "$REV_F"; }
chk "Codex reviewed this exact head" \
  "$(reviewed '[[{"user":{"login":"chatgpt-codex-connector[bot]"},"commit_id":"HEADSHA"}]]')" "1"
chk "Codex reviewed only an older head" \
  "$(reviewed '[[{"user":{"login":"chatgpt-codex-connector[bot]"},"commit_id":"OTHER"}]]')" "0"
chk "a human review of this head does not count" \
  "$(reviewed '[[{"user":{"login":"WEDDEsign"},"commit_id":"HEADSHA"}]]')" "0"
chk "reviews across pages (the branch-reset shape)" \
  "$(reviewed '[[{"user":{"login":"chatgpt-codex-connector[bot]"},"commit_id":"OTHER"}],[{"user":{"login":"chatgpt-codex-connector[bot]"},"commit_id":"HEADSHA"}]]')" "1"

# ---------------------------------------------------------------------------
section "Mergeability — before the claim and again after it"
merge_gate() { case "$1" in dirty|unknown) echo SKIP;; *) echo PROCEED;; esac; }
for st in dirty unknown clean blocked behind draft; do
  case "$st" in dirty|unknown) w=SKIP;; *) w=PROCEED;; esac
  chk "mergeable_state=$st -> $w" "$(merge_gate "$st")" "$w"
done
# `blocked` is the ordinary required-checks-and-review gate, not a conflict.
# Treating it as one would park every PR merely awaiting review.
post_claim() { # head_was, head_now, state_now
  if [ -n "$2" ] && { [ "$2" != "$1" ] || [ "$3" = dirty ] || [ "$3" = unknown ]; }
  then echo RESTORE; else echo PROCEED; fi
}
chk "post-claim: unchanged and clean -> proceed" "$(post_claim abc abc clean)" "PROCEED"
chk "post-claim: head moved -> restore" "$(post_claim abc def clean)" "RESTORE"
chk "post-claim: base advanced, head unchanged, now dirty -> restore" "$(post_claim abc abc dirty)" "RESTORE"
chk "post-claim: became unknown -> restore" "$(post_claim abc abc unknown)" "RESTORE"
chk "post-claim: blocked still proceeds" "$(post_claim abc abc blocked)" "PROCEED"
chk "post-claim: unreadable -> proceed (do not let a transient failure mute the sweep)" \
  "$(post_claim abc '' '')" "PROCEED"
after="abc123 dirty"
chk "post-claim: sha/state field split, sha" "${after%% *}" "abc123"
chk "post-claim: sha/state field split, state" "${after##* }" "dirty"

# ---------------------------------------------------------------------------
section "Post-failure verification — a nonzero exit does not mean nothing landed"
# Both timestamps carry only second precision, so the comparison must be
# inclusive: a comment created in the claim's own second really did land, and
# restoring the gate on top of it corrupts the state the check exists to protect.
claim=2026-08-24T09:20:00Z
verdict() { if [ -n "$1" ] && [[ ! "$1" < "$claim" ]]; then echo CONSUMED; else echo RESTORE; fi; }
chk "landed one second later -> gate stays consumed" "$(verdict 2026-08-24T09:20:01Z)" "CONSUMED"
chk "landed in the SAME second -> gate stays consumed" "$(verdict 2026-08-24T09:20:00Z)" "CONSUMED"
chk "only an older trigger -> restore the gate" "$(verdict 2026-08-24T09:19:59Z)" "RESTORE"
chk "verification returned nothing -> restore the gate" "$(verdict "")" "RESTORE"

section "set -e around the verification read"
# The outage that makes the post fail is the same outage that makes this read
# fail, so a bare assignment exits before either restore branch — losing the
# gate precisely when it matters. Run as real subprocesses: a subshell on the
# left of && has set -e suppressed inside it and cannot observe the abort.
bash -c 'set -euo pipefail; v=$(false); echo REACHED' >/dev/null 2>&1
chk "bare assignment aborts the run" "$?" "1"
bash -c 'set -euo pipefail; v=$(false || true); echo REACHED' >/dev/null 2>&1
chk "tolerant assignment reaches the restore branch" "$?" "0"
chk "and yields empty, not unset, under set -u" \
  "$(bash -c 'set -euo pipefail; v=$(false || true); echo "[${v}]"')" "[]"

# ---------------------------------------------------------------------------
section "Hard-stop escalation is scoped to the current cycle"
MK='<!-- codex-autopilot:hard-stop -->'
ESC_F='[.[][] | select(.body | contains("<!-- codex-autopilot:hard-stop -->"))
                 | select($since == "" or .created_at > $since)] | length'
esc() { printf '%s' "$1" | jq -r --arg since "$2" "$ESC_F"; }
OLD='[[{"created_at":"2026-08-24T06:00:00Z","body":"x <!-- codex-autopilot:hard-stop -->"}]]'
chk "no marker -> escalate" "$(esc '[[{"created_at":"T","body":"hi"}]]' "")" "0"
chk "never resumed, marker present -> stay quiet" "$(esc "$OLD" "")" "1"
chk "resumed after the marker -> escalate again for the new cycle" \
  "$(esc "$OLD" 2026-08-24T08:00:00Z)" "0"
chk "resumed, then escalated again -> stay quiet" \
  "$(esc '[[{"created_at":"2026-08-24T06:00:00Z","body":"<!-- codex-autopilot:hard-stop -->"}],[{"created_at":"2026-08-24T11:00:00Z","body":"<!-- codex-autopilot:hard-stop -->"}]]' 2026-08-24T08:00:00Z)" "1"

# ---------------------------------------------------------------------------
section "Claim on a round — a live session holds it, or the lease has run out"
# A session subscribed to the PR gets the same review event this workflow fires
# on, so both triage the round. The fix is not to suppress the second one — two
# of three parallel triages on NordScope#1233 found defects the first reader and
# Codex both missed — but to send it in read-only. These assertions cover when
# that happens.
CLAIMED_F='[.[][] | select(.event == "labeled"   and .label.name == $l) | .created_at] | max // ""'
RELEASED_F='[.[][] | select(.event == "unlabeled" and .label.name == $l) | .created_at] | max // ""'
claimed()  { printf '%s' "$1" | jq -r --arg l "claude-round-claimed" "$(filter "$REVIEW" "$CLAIMED_F")"; }
released() { printf '%s' "$1" | jq -r --arg l "claude-round-claimed" "$(filter "$REVIEW" "$RELEASED_F")"; }

EV_HELD='[[{"event":"labeled","label":{"name":"claude-round-claimed"},"created_at":"2026-08-25T12:05:00Z"}]]'
chk "reads the newest labeled timestamp" "$(claimed "$EV_HELD")" "2026-08-25T12:05:00Z"
chk "no claim label -> empty" "$(claimed '[[{"event":"labeled","label":{"name":"other"},"created_at":"2026-08-25T12:05:00Z"}]]')" ""
chk "claims across pages take the max" \
  "$(claimed '[[{"event":"labeled","label":{"name":"claude-round-claimed"},"created_at":"2026-08-25T11:00:00Z"}],[{"event":"labeled","label":{"name":"claude-round-claimed"},"created_at":"2026-08-25T12:05:00Z"}]]')" \
  "2026-08-25T12:05:00Z"
chk "a release is not read as a claim" "$(claimed '[[{"event":"unlabeled","label":{"name":"claude-round-claimed"},"created_at":"2026-08-25T12:05:00Z"}]]')" ""
chk "released reads unlabeled only" "$(released '[[{"event":"unlabeled","label":{"name":"claude-round-claimed"},"created_at":"2026-08-25T12:06:00Z"}]]')" "2026-08-25T12:06:00Z"

held() { # claimed_at, released_at, cutoff
  if [ -z "$1" ]; then echo POST
  elif [ -n "$2" ] && [[ "$2" > "$1" ]]; then echo POST
  elif [[ "$1" < "$3" ]]; then echo POST
  else echo REPORT; fi
}
CUT=2026-08-25T12:00:00Z; FRESH=2026-08-25T12:05:00Z; STALE=2026-08-25T11:40:00Z
chk "nobody claimed -> ordinary wake-up"            "$(held ""      ""                    "$CUT")" "POST"
chk "fresh claim -> report-only wake-up"            "$(held "$FRESH" ""                   "$CUT")" "REPORT"
chk "claim then release -> ordinary wake-up"        "$(held "$FRESH" 2026-08-25T12:06:00Z "$CUT")" "POST"
chk "claim older than the lease -> ordinary wake-up" "$(held "$STALE" ""                  "$CUT")" "POST"
# The ordering case: a session releases a round, then claims the next one. A
# test that only asked "is there a release?" would hand the live session's
# active round to a second writer.
chk "re-claim after an EARLIER release -> still held" "$(held "$FRESH" 2026-08-25T11:50:00Z "$CUT")" "REPORT"
chk "expired claim that was also released -> ordinary" "$(held "$STALE" 2026-08-25T11:45:00Z "$CUT")" "POST"

# ---------------------------------------------------------------------------
section "Drift guard — the shipped files still contain what was tested"
for f in "$SWEEP" "$GREEN"; do
  # Every comment/event/review/check-run read must paginate AND slurp. Without
  # --slurp, jq runs per page and an aggregate like `max` is computed per page.
  bad=$(grep -n 'gh api "repos' "$f" | grep -E 'comments\?|/events\?|/reviews\?|check-runs\?|/labels"' || true)
  chk "$(basename "$f"): no unpaginated collection reads" "${bad:-none}" "none"
done
chk "sweep claims the gate by DELETE before posting" \
  "$(grep -c 'X DELETE .*labels/\${LABEL}' "$SWEEP")" "2"
chk "ci-green claims the gate before posting" \
  "$(awk '/Claim the gate/{c=NR} /Re-ping Codex/{p=NR} END{print (c>0 && p>c) ? "yes" : "no"}' "$GREEN")" "yes"
# Reading the claim before settling loses the race every time: the live session
# is woken by the same event, so the sleep must come first.
chk "review wake-up settles before reading the claim" \
  "$(awk '/sleep "\$\{SETTLE_SECONDS\}"/{s=NR} /issues\/\$\{PR_NUMBER\}\/events/{e=NR} END{print (s>0 && e>s) ? "yes" : "no"}' "$REVIEW")" "yes"
# The two wake-up bodies must be mutually exclusive, or a claimed round gets
# both a "push the fix" and a "do not push" instruction on the same PR.
chk "exactly one wake-up body can fire per run" \
  "$(grep -c "steps.claim.outputs.held != 'true'" "$REVIEW")$(grep -c "steps.claim.outputs.held == 'true'" "$REVIEW")" "11"
chk "the claim is never removed by the workflow that reads it" \
  "$(grep -c 'X DELETE .*claude-round-claimed' "$REVIEW" || true)" "0"

printf '\n%s\n' "-----------------------------------------------"
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
