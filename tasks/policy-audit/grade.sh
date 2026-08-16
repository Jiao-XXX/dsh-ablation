#!/usr/bin/env bash
# Grade `policy-audit`: the exact set of incidents reportable under the policy.
#
# Expected: note_041, note_134, note_287, note_401.
#
# The four NON-reportable candidates each fail a different way, so the reported
# set diagnoses HOW a run went wrong rather than only that it did:
#
#   note_088  fails C2  -- 12-minute window        -> reported = ignored duration
#   note_179  fails C3  -- our own bucket          -> reported = ignored control
#   note_226  exception E2 -- NDA partner          -> reported = ignored E2
#   note_333  exception E1 -- pre-revoked value    -> reported = ignored E1
#
# A run that reports all eight candidates collapsed the conjunction to "was a
# secret exposed?". A run that reports fewer than four over-applied an
# exception. The per-clause echo below makes which of those happened readable
# straight out of runs.jsonl, without reopening the session log.
set -uo pipefail

EXPECTED=$'note_041\nnote_134\nnote_287\nnote_401'

if [ ! -f answer.txt ]; then
  echo "no answer.txt produced"
  echo "score=0 found=0/4 false_positives=0"
  exit 1
fi

ACTUAL=$(grep -oE 'note_[0-9]{3}' answer.txt | sort -u || true)

FOUND=$(comm -12 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | wc -l | tr -d ' ')
EXTRA=$(comm -13 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | wc -l | tr -d ' ')
MISSED=$(comm -23 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | tr '\n' ' ')

echo "found=$FOUND/4 false_positives=$EXTRA"
echo "missed: ${MISSED:-none}"
echo "reported: $(printf '%s' "$ACTUAL" | tr '\n' ' ')"

# Name the specific rule each false positive implies was dropped.
for pair in "note_088:C2-duration" "note_179:C3-our-control" \
            "note_226:E2-nda-partner" "note_333:E1-pre-revoked"; do
  id=${pair%%:*}; why=${pair##*:}
  if printf '%s\n' "$ACTUAL" | grep -qx "$id"; then
    echo "dropped_rule: $why (reported $id)"
  fi
done

if [ "$FOUND" -eq 4 ] && [ "$EXTRA" -eq 0 ]; then
  echo "score=1"
  exit 0
fi
echo "score=0"
exit 1
