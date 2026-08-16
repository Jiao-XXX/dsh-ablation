#!/usr/bin/env python3
"""Run the ablation matrix: every cell, every repeat, on one task.

Each run gets a FRESH workspace built by the task's `setup.sh`, so no cell
inherits state another cell left behind. The session log dsh writes is located
by diffing the sessions tree before and after the run rather than by guessing a
path, and both the proxy metrics and the task's own pass/fail grade are
recorded against it.

Cost control: this spends real tokens, once per (cell x repeat). Print the plan
with --dry-run before committing to a matrix.

Usage:
    run_matrix.py --task tasks/<id> [--cells b0-minimal,b1-standard,...]
                  [--repeats 3] [--out results/<name>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "bin"))
from extract_metrics import extract, read_events, sessions_root  # noqa: E402

SESSIONS = sessions_root()

# The full matrix in reporting order: the two baselines bracket the five single
# axes, and the replica sits last so a summary table reads as
# "Minimal ... Standard ... and here is where one-shot anchoring landed".
DEFAULT_CELLS = [
    "b0-minimal",
    "a1-tools",
    "a2-prompt",
    "a3-context",
    "a4-shell",
    "a5-fs",
    "b1-standard",
    "a1p-anchored",
]


def sessions_snapshot() -> set[Path]:
    """Every session directory currently on disk."""
    if not SESSIONS.exists():
        return set()
    return {d for ws in SESSIONS.iterdir() if ws.is_dir() for d in ws.iterdir() if d.is_dir()}


def encoded_workspace(workspace: Path) -> str:
    """The sessions-tree directory name dsh derives from a working directory.

    dsh keys session storage by cwd, encoding it by replacing every path
    separator with `-` and wrapping the result in `--`. Encoding is
    deterministic even though DECODING is not (a workspace whose own directory
    names contain hyphens cannot be recovered from the encoded form), which is
    why attribution runs in this direction only.
    """
    return "--" + str(workspace).lstrip("/").replace("/", "-") + "--"


def session_for(workspace: Path, before: set[Path]) -> Path | None:
    """The session this run produced, attributed by workspace rather than by time.

    A plain before/after diff of the whole sessions tree is WRONG on a machine
    where another dsh instance is live -- and one usually is, since the daily
    web server runs under launchd. Observed during the first matrix run: a
    99-second cell picked up an empty session the 3080 server had just created
    in an unrelated workspace, and reported that session's (absent) metrics as
    the cell's. Restricting the diff to this run's own workspace makes a
    concurrent instance irrelevant.
    """
    ws_dir = SESSIONS / encoded_workspace(workspace)
    if not ws_dir.is_dir():
        return None
    mine = [d for d in ws_dir.iterdir() if d.is_dir() and d not in before]
    if not mine:
        # The workspace is unique per (cell, repeat), so anything already there
        # belongs to an earlier attempt at this same cell; the newest is still
        # the right answer.
        mine = [d for d in ws_dir.iterdir() if d.is_dir()]
    return max(mine, key=lambda p: p.stat().st_mtime) if mine else None


def run_cell(cell: str, task_dir: Path, task_text: str, workspace: Path, timeout: int) -> dict:
    """Run one cell once and return its raw record."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    setup = task_dir / "setup.sh"
    if setup.exists():
        subprocess.run(["bash", str(setup)], cwd=workspace, check=True,
                       capture_output=True, timeout=600)

    env = {
        **os.environ,
        # PATH is inherited, never rewritten: prepending one machine's Node
        # install would silently pick a different runtime than the `dsh` the
        # caller verified with `doctor`. Node selection belongs to the shell.
        #
        # Without this the sandbox asks for approval, and a headless run has no
        # answerer -- the process would hang until the timeout instead of
        # producing a datapoint. `never` is the same policy the danger-full-access
        # preset applies, chosen here so refusals never confound the axes.
        "DSH_PERMISSION_MODE": "danger-full-access",
    }
    # Pin the tools presentation mode: left unset it is read from the ambient
    # environment, which would silently vary between the shell that ran cell 1
    # and the shell that ran cell 8.
    env.pop("DSH_TOOLS_MODE", None)

    before = sessions_snapshot()
    started = time.time()
    try:
        proc = subprocess.run(
            # `_base-standard.yml` comes FIRST for every cell: it corrects the
            # bare base+headless composition into the official standard preset
            # (see that file). Without it each axis would be measured against a
            # baseline that is two tools and one schema away from Standard.
            ["dsh", "--profile", "ablation",
             "--patch", str(ROOT / "overlays" / "_base-standard.yml"),
             "--patch", str(ROOT / "overlays" / f"{cell}.yml"), task_text],
            cwd=workspace, env=env, capture_output=True, text=True, timeout=timeout,
        )
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code, timed_out = -1, True
    elapsed = time.time() - started

    session = session_for(workspace, before)
    record = {
        "cell": cell,
        "exit_code": code,
        "timed_out": timed_out,
        "elapsed_s": round(elapsed, 1),
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
        "workspace": str(workspace),
        "session_dir": str(session) if session else None,
    }

    if session:
        try:
            metrics = extract(read_events(session))
            metrics["session_id"] = session.name.removeprefix("session-")
            record["metrics"] = metrics
        except Exception as exc:  # a broken log must not abort the matrix
            record["metrics_error"] = f"{type(exc).__name__}: {exc}"

    grade = task_dir / "grade.sh"
    if grade.exists():
        try:
            g = subprocess.run(["bash", str(grade)], cwd=workspace,
                               capture_output=True, text=True, timeout=600)
            record["grade"] = {
                "passed": g.returncode == 0,
                "exit_code": g.returncode,
                "stdout": g.stdout[-2000:],
            }
        except subprocess.TimeoutExpired:
            record["grade"] = {"passed": False, "exit_code": -1, "stdout": "grade timed out"}

    return record


def summarize(records: list[dict]) -> str:
    """Per-cell aggregate table, with spread so a single run is never read as a result."""
    by_cell: dict[str, list[dict]] = {}
    for rec in records:
        by_cell.setdefault(rec["cell"], []).append(rec)

    def agg(values, width=13):
        """Mean±sd, or the bare value when a cell ran once.

        Spread is never optional here. A single number invites reading a
        one-run difference as an effect, and several of these metrics carry a
        standard deviation larger than the gaps between cells.
        """
        clean = [v for v in values if v is not None]
        if not clean:
            return "-".rjust(width)
        if len(clean) == 1:
            return f"{clean[0]:g}".rjust(width)
        return f"{statistics.mean(clean):.1f}±{statistics.pstdev(clean):.1f}".rjust(width)

    lines = [
        f"{'cell':<14}{'n':>3}{'pass':>7}{'tools':>9}{'sysC':>11}{'cats':>8}"
        f"{'steps':>11}{'calls':>11}{'out_tok':>15}{'pruned':>9}{'sec':>13}",
        "-" * 122,
    ]
    # Known cells first in their reporting order, then anything else this run
    # contained. Iterating DEFAULT_CELLS alone would silently omit a cell that
    # is not in the standard matrix -- which is exactly what the dose-response
    # cells are.
    ordered = [c for c in DEFAULT_CELLS if c in by_cell]
    ordered += [c for c in by_cell if c not in DEFAULT_CELLS]
    for cell in ordered:
        recs = by_cell.get(cell)
        if not recs:
            continue
        ms = [r.get("metrics") or {} for r in recs]
        graded = [r["grade"]["passed"] for r in recs if "grade" in r]
        passes = f"{sum(graded)}/{len(graded)}" if graded else "-"
        first_hdr = [m["headers"][0] if m.get("headers") else None for m in ms]
        lines.append(
            f"{cell:<14}{len(recs):>3}{passes:>7}"
            f"{agg([h['tool_count'] if h else None for h in first_hdr], 9)}"
            f"{agg([h['system_chars'] if h else None for h in first_hdr], 11)}"
            f"{agg([m.get('distinct_catalogs') for m in ms], 8)}"
            f"{agg([m.get('steps') for m in ms], 11)}"
            f"{agg([m.get('tool_calls_total') for m in ms], 11)}"
            f"{agg([(m.get('usage') or {}).get('output') for m in ms], 15)}"
            f"{agg([(m.get('context_destruction') or {}).get('prune_shadowed_tokens') for m in ms], 9)}"
            f"{agg([r.get('elapsed_s') for r in recs], 13)}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True, help="task directory")
    parser.add_argument("--cells", default=",".join(DEFAULT_CELLS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800, help="per-run seconds")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    task_dir = args.task if args.task.is_absolute() else ROOT / args.task
    task_text = (task_dir / "task.md").read_text(encoding="utf-8").strip()
    cells = [c.strip() for c in args.cells.split(",") if c.strip()]

    for cell in cells:
        overlay = ROOT / "overlays" / f"{cell}.yml"
        if not overlay.exists():
            raise SystemExit(f"no overlay for cell {cell!r}: {overlay}")

    total = len(cells) * args.repeats
    print(f"task    {task_dir.name}")
    print(f"cells   {cells}")
    print(f"repeats {args.repeats}   -> {total} model runs")
    if args.dry_run:
        print("\n(dry run -- nothing executed)")
        return 0

    out_dir = args.out or (ROOT / "results" / f"{task_dir.name}-{int(time.time())}")
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "runs.jsonl"

    records: list[dict] = []
    for rep in range(args.repeats):
        for cell in cells:
            workspace = ROOT / "workspaces" / out_dir.name / f"{cell}-r{rep}"
            print(f"[{len(records) + 1}/{total}] {cell} rep{rep} ... ", end="", flush=True)
            rec = run_cell(cell, task_dir, task_text, workspace, args.timeout)
            rec["repeat"] = rep
            records.append(rec)
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            m = rec.get("metrics") or {}
            hdr = (m.get("headers") or [{}])[0]
            verdict = "PASS" if rec.get("grade", {}).get("passed") else (
                "fail" if "grade" in rec else "-")
            print(f"{verdict}  tools={hdr.get('tool_count')} "
                  f"cats={m.get('distinct_catalogs')} steps={m.get('steps')} "
                  f"{rec['elapsed_s']}s")

    summary = summarize(records)
    (out_dir / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary)
    print(f"\nwrote {jsonl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
