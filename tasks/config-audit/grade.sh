#!/usr/bin/env bash
# Grade the `config-audit` task: exact set of violating service ids.
#
# Scored as all-or-nothing on the SET, with the partial count printed so a
# near-miss is distinguishable from a total miss in the runs.jsonl record. A
# cell that finds two of three violations has not "mostly passed" -- it has
# shipped a clean audit report with a hole in it, which is the failure mode
# this task exists to detect -- but the partial count is what shows how much of
# the middle survived.
set -uo pipefail

EXPECTED=$'svc_118\nsvc_147\nsvc_176'

if [ ! -f answer.txt ]; then
  echo "no answer.txt produced"
  echo "score=0 found=0 expected=3"
  exit 1
fi

# Normalize: strip blanks and surrounding whitespace, sort, dedupe.
ACTUAL=$(grep -oE 'svc_[0-9]{3}' answer.txt | sort -u || true)

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
