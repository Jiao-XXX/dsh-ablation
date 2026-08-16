#!/usr/bin/env python3
"""Re-score a finished matrix under an alternative ground truth.

Why this exists: `incident-triage`'s third planted target turned out to be
interpretively ambiguous. Observed in run 10 (a1-tools rep1), the model found
note_041 and note_068, then spent 38k reasoning tokens arguing note_093 OUT --
a contractor's unwiped laptop went back to the organization, so on its reading
no credential reached anyone it should not have. That is a defensible position,
not a memory failure, and a grader that calls it wrong injects noise which is
uncorrelated with any experimental axis.

The fix is not to pick whichever scoring flatters a conclusion. It is to report
BOTH, so the reader can see whether a cell-to-cell difference survives the
choice. A difference that appears under one ground truth and vanishes under the
other was never about the axis.

Re-scoring needs no re-runs: grade.sh records the full reported set, so the
model's answer is recoverable from runs.jsonl.

Usage:
    rescore.py [results/<run-dir>]
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
from run_matrix import DEFAULT_CELLS  # noqa: E402

# This script's ground truths are specific to ONE task. Pointed at another
# task's results it would happily print a table of meaningless numbers -- every
# run scored against an answer key from a different fixture, and nothing in the
# output would say so. Refusing is the only safe behaviour.
TASK = "incident-triage"

# The item that turned out to be a judgement call rather than a recall test.
AMBIGUOUS = "note_093"

GROUND_TRUTHS = {
    "strict (as authored)": {"note_041", "note_068", "note_093"},
    f"minus {AMBIGUOUS}": {"note_041", "note_068"},
}


def reported_set(record: dict) -> set[str] | None:
    """Recover the ids the model actually reported, from the grader's echo."""
    stdout = (record.get("grade") or {}).get("stdout") or ""
    match = re.search(r"reported:(.*)", stdout)
    if not match:
        return None
    return set(re.findall(r"note_\d{3}", match.group(1)))


def main() -> int:
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
        if not run_dir.is_absolute():
            run_dir = ROOT / run_dir
    else:
        runs = sorted((ROOT / "results").glob("*/runs.jsonl"), key=lambda p: p.stat().st_mtime)
        if not runs:
            raise SystemExit("no results/*/runs.jsonl found")
        run_dir = runs[-1].parent

    # `results/<task>-<timestamp>`; anything else is not a run directory.
    task = run_dir.name.rsplit("-", 1)[0]
    if task != TASK:
        raise SystemExit(
            f"refusing to rescore: this script holds the answer key for {TASK!r}, "
            f"but {run_dir.name!r} is a {task!r} run.\n"
            f"Scoring one task's answers against another's key produces a table "
            f"of numbers that look fine and mean nothing."
        )

    records = [
        json.loads(line)
        for line in (run_dir / "runs.jsonl").read_text().splitlines() if line.strip()
    ]

    print(f"run  {run_dir}\n")
    for label, truth in GROUND_TRUTHS.items():
        print(f"=== ground truth: {label}  {sorted(truth)} ===")
        print(f"{'cell':<14}{'n':>3}{'pass':>8}{'rate':>7}   per-run reported")
        for cell in DEFAULT_CELLS:
            recs = [r for r in records if r["cell"] == cell]
            if not recs:
                continue
            outcomes, detail = [], []
            for rec in recs:
                got = reported_set(rec)
                if got is None:
                    detail.append("?")
                    continue
                # Under a reduced ground truth an id outside it is not a false
                # positive if it was a planted target -- the model was asked
                # about credential exposure, not about this file's answer key.
                # Only ids outside the AUTHORED set count against a run.
                spurious = got - GROUND_TRUTHS["strict (as authored)"]
                ok = truth.issubset(got) and not spurious
                outcomes.append(ok)
                detail.append("".join(
                    sorted(i.replace("note_", "") for i in got)) or "-")
            if not outcomes:
                continue
            rate = statistics.mean(outcomes)
            print(f"{cell:<14}{len(outcomes):>3}{sum(outcomes):>4}/{len(outcomes):<3}"
                  f"{rate:>6.0%}   {' '.join(detail)}")
        print()

    print("A cell whose verdict changes between the two blocks was scored by the")
    print("ambiguous item, not by the axis it is named after.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
