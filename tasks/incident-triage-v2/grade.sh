#!/usr/bin/env bash
# Grade `incident-triage-v2`: exact set of 6 credential-exposure notes.
#
# Recall and precision are reported separately and BOTH are printed, because
# they fail for different reasons and the axes are expected to move them
# differently: a missed target deep in the file points at recall over a long
# record, while a reported near-miss points at judgement under a prompt that
# encourages pattern-matching. Collapsing them into one bit would hide which
# of the two an axis actually moved.
#
# The run still passes only on an exact match. A six-item audit that finds five
# is a clean-looking report with a hole in it.
set -uo pipefail

EXPECTED=$'note_038\nnote_112\nnote_186\nnote_263\nnote_341\nnote_418'

if [ ! -f answer.txt ]; then
  echo "no answer.txt produced"
  echo "score=0 found=0/6 false_positives=0"
  exit 1
fi

ACTUAL=$(grep -oE 'note_[0-9]{3}' answer.txt | sort -u || true)

FOUND=$(comm -12 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | wc -l | tr -d ' ')
EXTRA=$(comm -13 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | wc -l | tr -d ' ')
MISSED=$(comm -23 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | tr '\n' ' ')

echo "found=$FOUND/6 false_positives=$EXTRA"
echo "missed: ${MISSED:-none}"
echo "reported: $(printf '%s' "$ACTUAL" | tr '\n' ' ')"

if [ "$FOUND" -eq 6 ] && [ "$EXTRA" -eq 0 ]; then
  echo "score=1"
  exit 0
fi
echo "score=0"
exit 1
