#!/usr/bin/env bash
# Grade the `incident-triage` task: exact set of credential-exposure notes.
#
# The partial count matters more here than in config-audit. Because the three
# targets sit at different depths of the file, WHICH ones a cell finds says
# where its record was intact: losing only note_068 and note_093 while keeping
# note_041 is the signature of a head-retaining prune, not of weak judgement.
# runs.jsonl keeps the reported set so that pattern stays recoverable.
set -uo pipefail

EXPECTED=$'note_041\nnote_068\nnote_093'

if [ ! -f answer.txt ]; then
  echo "no answer.txt produced"
  echo "score=0 found=0 expected=3"
  exit 1
fi

ACTUAL=$(grep -oE 'note_[0-9]{3}' answer.txt | sort -u || true)

FOUND=$(comm -12 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | wc -l | tr -d ' ')
EXTRA=$(comm -13 <(printf '%s\n' "$EXPECTED" | sort) <(printf '%s\n' "$ACTUAL") | wc -l | tr -d ' ')

echo "found=$FOUND/3 false_positives=$EXTRA"
echo "reported: $(printf '%s' "$ACTUAL" | tr '\n' ' ')"

if [ "$FOUND" -eq 3 ] && [ "$EXTRA" -eq 0 ]; then
  echo "score=1"
  exit 0
fi
echo "score=0"
exit 1
