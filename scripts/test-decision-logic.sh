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
DRIFT_LOG=$(mktemp)
trap 'rm -f "$DRIFT_LOG"' EXIT
SWEEP=.github/workflows/sweep-stalled-repings.yml
GREEN=.github/workflows/wake-on-ci-green.yml
THREADS=.github/workflows/sweep-unresolved-threads.yml

chk() { # name, got, want
  if [ "$2" = "$3" ]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
  else fail=$((fail+1)); printf '  FAIL %s\n         got:  %s\n         want: %s\n' "$1" "$2" "$3"; fi
}

# filter FILE 'jq filter' — assert the filter appears verbatim in FILE, then
# echo it so the caller can run the very string it just verified ships.
filter() {
  local file="$1" f="$2" content
  # Substring match over the WHOLE file, not `grep -qF`. grep is line-based,
  # and a multi-line -F pattern is treated as one fixed string PER LINE — it
  # matches if ANY line appears anywhere. So a multi-line filter was "verified"
  # by its least distinctive line (`| length` matches almost anything), and a
  # real change to the shipped logic drifted silently. The single-line filters
  # this harness started with were guarded correctly; the guard only became
  # theatre when multi-line ones were registered with it.
  content=$(cat "$file")
  if [[ "$content" != *"$f"* ]]; then
    # Reported on stderr and recorded in a file, not in `fail`. Every call site
    # is `X=$(filter ...)`, so this body runs in a command-substitution
    # subshell: `fail=$((fail+1))` there is discarded when the subshell exits,
    # and anything printed on stdout is captured into the caller's variable
    # instead of shown. Until both were fixed, a drift failed the suite only
    # when it happened to break an assertion as well — a guard that reported
    # itself and then exited 0.
    printf '  FAIL drift: filter not found in %s\n         %s\n' "$file" "$f" >&2
    printf 'x\n' >> "$DRIFT_LOG"
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
section "The author-side set is built, not assumed"
# `pr_author` is `// ""` when the account has been deleted — and so is the
# login on a comment left by one. An empty `$me` would therefore match an empty
# last-author and read that thread as ANSWERED, which silences the sweep on
# exactly the PR whose author is gone. The assertions below the fold were
# passing while that was true, because they hand the filter a set rather than
# building one.
SIDE_F=$(filter "$THREADS" '[$me, "claude[bot]"] + $extra | map(select(. != ""))')
side() { jq -c -n --arg me "$1" --argjson extra "${2:-[]}" "$SIDE_F"; }

chk "the author and the bot" "$(side WEDDEsign)" '["WEDDEsign","claude[bot]"]'
chk "extras are appended" "$(side WEDDEsign '["org-bot"]')" '["WEDDEsign","claude[bot]","org-bot"]'
chk "a deleted author drops out rather than matching every deleted commenter" \
  "$(side "")" '["claude[bot]"]'
chk "an empty extra is dropped too" "$(side WEDDEsign '[""]')" '["WEDDEsign","claude[bot]"]'

# ---------------------------------------------------------------------------
section "Unresolved threads — who spoke last decides whether to wake"
# The discriminator between "nobody answered" and "a conversation is in
# progress". `auto-resolve-review-threads` accepts the PR author, claude[bot]
# and any configured `extra_identities` as a resolve signal, so a thread any of
# them spoke on LAST is one where the answer exists and the resolve is what
# failed — reportable, but not something to wake a session onto. The sweep must
# use the SAME set: a login the resolver treats as author-side and this one does
# not is a thread that gets a wake-up nobody needed.
KEYS_F=$(filter "$THREADS" '.[]
              | select(.isResolved == false)
              | select((.comments.nodes[-1].author.login // "") as $last
                       | ($side | index($last)) | not)
              | "\(.id) \(.comments.nodes[-1].id // "-")"')
UNRESOLVED_F=$(filter "$THREADS" '[.[] | select(.isResolved == false)] | length')

th() { # id, isResolved, last-author, last-comment-id
  printf '{"id":"%s","isResolved":%s,"comments":{"nodes":[{"id":"%s","author":{"login":"%s"}}]}}' \
    "$1" "$2" "$4" "$3"
}
doc() { printf '[%s]' "$(IFS=,; echo "$*")"; }

SIDE='["WEDDEsign","claude[bot]"]'
keys() { printf '%s' "$1" | jq -r --argjson side "${2:-$SIDE}" "$KEYS_F"; }
unanswered() { printf '%s' "$(keys "$1" "${2:-$SIDE}")" | grep -c . || true; }
unresolved() { printf '%s' "$1" | jq -r "$UNRESOLVED_F"; }

REVIEWER=$(doc "$(th T1 false chatgpt-codex-connector C1)")
AUTHOR=$(doc "$(th T1 false WEDDEsign C1)")
BOT=$(doc "$(th T1 false 'claude[bot]' C1)")
DONE=$(doc "$(th T1 true chatgpt-codex-connector C1)")
MIXED=$(doc "$(th T1 false WEDDEsign C1)" "$(th T2 false chatgpt-codex-connector C2)")

chk "reviewer spoke last -> wake" "$(unanswered "$REVIEWER")" "1"
chk "author spoke last -> report only" "$(unanswered "$AUTHOR")" "0"
chk "claude[bot] spoke last -> report only" "$(unanswered "$BOT")" "0"
chk "resolved threads are never counted" "$(unanswered "$DONE")" "0"
chk "resolved threads are not unresolved either" "$(unresolved "$DONE")" "0"
chk "mixed: only the unanswered one wakes" "$(unanswered "$MIXED")" "1"
chk "mixed: both count as unresolved" "$(unresolved "$MIXED")" "2"
# A thread with no comments cannot happen via the UI, but a null author (a
# deleted account) can. `// ""` must keep it counted rather than crashing the
# filter — erring toward waking, since the alternative is a silent block.
NULL_AUTHOR=$(doc '{"id":"T1","isResolved":false,"comments":{"nodes":[{"id":"C1","author":null}]}}')
chk "null author reads as not-the-author -> wake" "$(unanswered "$NULL_AUTHOR")" "1"
# The same thread on a PR whose own author is deleted. This is the case the
# hand-written set above cannot reach: it only breaks once `$me` is built from
# an empty `pr_author`, which is why the set construction is asserted on its
# own further up.
chk "a deleted author does not make a deleted commenter author-side" \
  "$(unanswered "$NULL_AUTHOR" "$(side "")")" "1"
# The `extra_identities` case: the caller template configures WEDDEsign on the
# resolver, and on a PR opened by anyone else that login is author-side there.
ORG=$(doc "$(th T1 false WEDDEsign C1)")
chk "configured extra identity spoke last -> report only" \
  "$(unanswered "$ORG" '["someone-else","claude[bot]","WEDDEsign"]')" "0"
chk "the same login unconfigured -> wake" \
  "$(unanswered "$ORG" '["someone-else","claude[bot]"]')" "1"

# ---------------------------------------------------------------------------
section "Drafts are not swept"
# A draft cannot merge at all, so an unanswered thread is not what is blocking
# it and the wake would name the wrong cause. The ceremony docs reserve
# `--draft` for code that should not be reviewed yet, which is the same call
# made from the other side.
DRAFT_F=$(filter "$THREADS" '.[] | select(.draft != true) | .number')
prs() { printf '%s' "$1" | jq -r "$DRAFT_F" | paste -sd, -; }

chk "ready PRs are swept" "$(prs '[{"number":1,"draft":false}]')" "1"
chk "drafts are skipped" "$(prs '[{"number":1,"draft":true}]')" ""
chk "mixed: only the ready one" \
  "$(prs '[{"number":1,"draft":true},{"number":2,"draft":false}]')" "2"
# The field is always present on this endpoint, but a missing one must sweep
# rather than skip: erring toward looking is how a recovery net fails safe.
chk "an absent draft field is not a draft" "$(prs '[{"number":1}]')" "1"

# ---------------------------------------------------------------------------
section "Unresolved-thread keys identify the thread AND its last comment"
# Keying the marker on the head alone suppressed a thread OPENED AFTER the
# report, forever — the blocked-with-no-wake state the sweep exists to recover.
# Both halves of the key are what fix it.
chk "the key pairs thread with its last comment" \
  "$(keys "$REVIEWER")" "T1 C1"
chk "a new thread on the same head is a new key" \
  "$(keys "$(doc "$(th T1 false codex C1)" "$(th T2 false codex C2)")" | paste -sd, -)" "T1 C1,T2 C2"
chk "a new comment on the same thread is a new key" \
  "$(keys "$(doc "$(th T1 false codex C9)")")" "T1 C9"
# `comments(last:1)` cannot be empty for a real thread, but a missing id must
# still produce a key rather than a null that silently collapses two threads.
chk "a missing comment id still yields a key" \
  "$(keys "$(doc '{"id":"T1","isResolved":false,"comments":{"nodes":[{"author":{"login":"codex"}}]}}')")" "T1 -"

# ---------------------------------------------------------------------------
section "The sweep stands down only when every key was already reported"
SEEN_F=$(filter "$THREADS" '[.[][] | .body | select(contains($p)) | capture($p + "(?<k>[0-9a-f,]+)").k]
                | join(",")')
FRESH_F=$(filter "$THREADS" '($seen | split(",")) as $have
              | [($tokens | split(","))[] | . as $t | select($have | index($t) | not)]
              | length')
seen() { printf '%s' "$1" | jq -r --arg p "$2" "$SEEN_F"; }
fresh() { jq -r -n --arg tokens "$1" --arg seen "$2" "$FRESH_F"; }

P_OLD='codex-autopilot:unresolved-threads aaaaaaaaaa keys='
P_NEW='codex-autopilot:unresolved-threads bbbbbbbbbb keys='
HIST="[[{\"body\":\"woken <!-- ${P_OLD}11111111,22222222 -->\"}]]"

chk "keys are read back off this head's marker" "$(seen "$HIST" "$P_OLD")" "11111111,22222222"
chk "a new head has no marker of its own" "$(seen "$HIST" "$P_NEW")" ""
chk "no history -> nothing reported" "$(seen '[[]]' "$P_OLD")" ""
chk "two markers on one head union their keys" \
  "$(seen "[[{\"body\":\"<!-- ${P_OLD}11111111 -->\"},{\"body\":\"<!-- ${P_OLD}33333333 -->\"}]]" "$P_OLD")" \
  "11111111,33333333"
# A body mentioning the prefix without a key list must not blow up the read.
chk "a prefix with no keys contributes nothing" \
  "$(seen "[[{\"body\":\"see ${P_OLD}\"}]]" "$P_OLD")" ""

chk "unchanged state -> stand down" "$(fresh "11111111,22222222" "11111111,22222222")" "0"
chk "a subset (some threads answered) -> stand down, no wake-storm" \
  "$(fresh "22222222" "11111111,22222222")" "0"
chk "a new thread or a new comment -> wake" \
  "$(fresh "11111111,44444444" "11111111,22222222")" "1"
chk "nothing reported yet -> wake" "$(fresh "11111111" "")" "1"
chk "every key new -> wake" "$(fresh "55555555,66666666" "11111111")" "2"

# ---------------------------------------------------------------------------
section "Green means the NEWEST run of each required check, not any of them"
# A re-run adds a second check run under the same name on the same SHA. Asking
# whether ANY run succeeded lets a stale success outvote the failing re-run
# that replaced it — and this sweep's whole output is the claim "green but
# unmergeable", posted onto a PR that would in fact be red. `filter=latest` in
# the URL already collapses this; the filter does not depend on it.
MISSING_F=$(filter "$THREADS" '.[] as $req
              | select((($have | map(select(.name == $req)) | sort_by(.started_at, .id)
                         | last | .conclusion) == "success") | not)
              | $req')
run() { # name, conclusion, started_at, id
  printf '{"name":"%s","conclusion":%s,"started_at":"%s","id":%s}' "$1" "$2" "$3" "$4"
}
missing() { printf '%s' "$1" | jq -r --argjson have "$2" "$MISSING_F" | paste -sd, -; }
REQ='["pytest","build"]'

chk "all required checks green -> nothing missing" \
  "$(missing "$REQ" "[$(run pytest '"success"' 2026-08-25T10:00:00Z 1),$(run build '"success"' 2026-08-25T10:00:00Z 2)]")" ""
chk "a required check absent -> missing" \
  "$(missing "$REQ" "[$(run pytest '"success"' 2026-08-25T10:00:00Z 1)]")" "build"
chk "a stale success does not outvote a failing re-run" \
  "$(missing "$REQ" "[$(run pytest '"success"' 2026-08-25T10:00:00Z 1),$(run pytest '"failure"' 2026-08-25T11:00:00Z 9),$(run build '"success"' 2026-08-25T10:00:00Z 2)]")" "pytest"
chk "a stale success does not outvote a re-run still in progress" \
  "$(missing "$REQ" "[$(run pytest '"success"' 2026-08-25T10:00:00Z 1),$(run pytest null 2026-08-25T11:00:00Z 9),$(run build '"success"' 2026-08-25T10:00:00Z 2)]")" "pytest"
chk "a newer success clears an older failure" \
  "$(missing "$REQ" "[$(run pytest '"failure"' 2026-08-25T10:00:00Z 1),$(run pytest '"success"' 2026-08-25T11:00:00Z 9),$(run build '"success"' 2026-08-25T10:00:00Z 2)]")" ""
# Re-runs started inside the same second are ordered by id, which GitHub
# assigns monotonically — without the tiebreak, `last` would be arbitrary.
chk "same started_at -> the higher check-run id wins" \
  "$(missing "$REQ" "[$(run pytest '"success"' 2026-08-25T10:00:00Z 1),$(run pytest '"failure"' 2026-08-25T10:00:00Z 9),$(run build '"success"' 2026-08-25T10:00:00Z 2)]")" "pytest"

# ---------------------------------------------------------------------------
section "Drift guard — the shipped files still contain what was tested"
for f in "$SWEEP" "$GREEN" "$THREADS"; do
  # Every comment/event/review/check-run read must paginate AND slurp. Without
  # --slurp, jq runs per page and an aggregate like `max` is computed per page.
  bad=$(grep -n 'gh api "repos' "$f" | grep -E 'comments\?|/events\?|/reviews\?|check-runs\?|/labels"' || true)
  chk "$(basename "$f"): no unpaginated collection reads" "${bad:-none}" "none"
done
# Two invariants of the threads sweep that are shell/GraphQL rather than jq, so
# no assertion above can reach them. Both are silent under-reads when removed —
# the sweep goes quiet on exactly the PRs it exists for — which is why they are
# pinned here rather than left to review.
chk "threads sweep pins filter=latest on the check-run read" \
  "$(grep -c 'check-runs?per_page=100&filter=latest' "$THREADS")" "1"
chk "threads sweep paginates the review-thread connection" \
  "$(grep -c 'reviewThreads(first:100, after:$cursor)' "$THREADS")" "1"
chk "threads sweep asks for the page cursor it pages on" \
  "$(grep -c 'pageInfo { hasNextPage endCursor }' "$THREADS")" "1"
# A read failure has to reach the run's CONCLUSION, not just the summary: the
# retry note is only true for a transient failure, and a permission or token
# problem fails identically on every tick while the run stays green. Shell, not
# jq, so this is the only thing that can hold it.
chk "threads sweep counts a failed thread read" \
  "$(grep -c 'read_fail=$((read_fail + 1))' "$THREADS")" "1"
chk "threads sweep exits nonzero on a failed thread read" \
  "$(grep -c 'had_fatal}" -ne 0 ] || \[ "${read_fail}" -ne 0 \]' "$THREADS")" "1"
chk "threads sweep does not claim a clean sweep after a failed read" \
  "$(grep -c 'woke}" = "0" \] && \[ "${had_fatal}" = "0" \] && \[ "${read_fail}" = "0" \]' "$THREADS")" "1"
chk "sweep claims the gate by DELETE before posting" \
  "$(grep -c 'X DELETE .*labels/\${LABEL}' "$SWEEP")" "2"
chk "ci-green claims the gate before posting" \
  "$(awk '/Claim the gate/{c=NR} /Re-ping Codex/{p=NR} END{print (c>0 && p>c) ? "yes" : "no"}' "$GREEN")" "yes"

# Drift is counted here rather than in `filter`, for the subshell reason above.
fail=$((fail + $(grep -c . "$DRIFT_LOG" 2>/dev/null || true)))

printf '\n%s\n' "-----------------------------------------------"
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
