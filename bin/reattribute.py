#!/usr/bin/env python3
"""Re-attribute a matrix run's sessions by workspace, then re-summarize.

A matrix run recorded before the workspace-attribution fix located each cell's
session by diffing the whole sessions tree. That is unreliable on a machine
running another dsh instance -- the daily launchd web server creates sessions
of its own -- so a slow cell can capture a stranger's session and report its
metrics. Observed once: a 99-second cell attributed an empty session belonging
to the 3080 server, reporting `steps=0` for a run that had actually taken 7.

Nothing is lost when that happens. dsh keys session storage by cwd, and every
(cell, repeat) ran in its own unique workspace, so the correct session is still
on disk and recoverable by encoding the workspace path forward.

This rewrites `runs.jsonl` in place (keeping a `.bak`) and reprints the summary.

Usage:
    reattribute.py [results/<run-dir>]      # newest run if omitted
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
from extract_metrics import extract, read_events  # noqa: E402
from run_matrix import SESSIONS, encoded_workspace, summarize  # noqa: E402


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

    jsonl = run_dir / "runs.jsonl"
    records = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]

    repaired = 0
    for rec in records:
        workspace = Path(rec["workspace"])
        ws_dir = SESSIONS / encoded_workspace(workspace)
        if not ws_dir.is_dir():
            rec["attribution"] = "no-session-dir"
            continue
        candidates = [d for d in ws_dir.iterdir() if d.is_dir()]
        if not candidates:
            rec["attribution"] = "no-session-dir"
            continue
        correct = max(candidates, key=lambda p: p.stat().st_mtime)

        was = rec.get("session_dir")
        if was == str(correct) and rec.get("metrics", {}).get("steps"):
            rec["attribution"] = "unchanged"
            continue

        rec["session_dir_before_repair"] = was
        rec["session_dir"] = str(correct)
        rec["attribution"] = "repaired"
        try:
            metrics = extract(read_events(correct))
            metrics["session_id"] = correct.name.removeprefix("session-")
            rec["metrics"] = metrics
            rec.pop("metrics_error", None)
        except Exception as exc:
            rec["metrics_error"] = f"{type(exc).__name__}: {exc}"
        repaired += 1

    shutil.copy(jsonl, jsonl.with_suffix(".jsonl.bak"))
    with jsonl.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = summarize(records)
    (run_dir / "summary.txt").write_text(summary + "\n", encoding="utf-8")

    print(f"run       {run_dir}")
    print(f"records   {len(records)}   repaired {repaired}\n")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
