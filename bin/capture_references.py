#!/usr/bin/env python3
"""Collect the uncontaminated preset references and run the admission gate.

The testbed's B0/B1 cells claim to reproduce the official `minimal` and
`standard` agent presets. Only a real preset-composed session can settle that,
and only the Web surface composes presets -- `dsh-agent-presets` is mounted by
the `dsh-web-app` bundle alone, and a session's preset arrives as an
`agent-preset/selected` event that the api-proxy writes. Neither headless nor
the SDK JSON-RPC surface can select one.

So the references come from a throwaway Web profile holding NOTHING but
dsh-base and dsh-web-app. This script finds those sessions, records them, and
compares each against the matching ablation cell.

Usage:
    capture_references.py                 # find refs, then gate B0 and B1
    capture_references.py --list          # just show what was found
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "reference"

sys.path.insert(0, str(ROOT / "bin"))
from extract_metrics import read_events, sessions_root, unpack  # noqa: E402

SESSIONS = sessions_root()

# Deltas that are expected by construction rather than by mistake. The Web
# surface registers prompt sections that simply do not exist without it
# (dsh-web-app's `surfaceContext` rows contribute the harness-checkout and
# web-surface paragraphs), so a headless cell can never carry them. Declaring
# them here keeps the gate strict about everything else -- an undeclared
# difference still fails.
ACCEPTED_DELTAS = [
    # The harness-checkout paragraph and the Web GUI orientation paragraph.
    "implementation checkout is at",
    "DeepSeek Harness Web GUI",
    "DSH_WEB_URL",
    # "format them as Markdown inline code ... clickable in Web" -- advice about
    # rendering in a GUI that a headless run does not have.
    "mention the primary outputs in your final response",
]


def describe(session_dir: Path, default_preset: str) -> dict | None:
    """Summarize a session: which preset composed it, and its first header.

    A session that never emitted `agent-preset/selected` was composed by the
    roster's configured default (`config.default`, `standard` in the shipped
    web-app composition) -- the UI writes a selection event only when the user
    picks something other than the default. Attributing those to the default is
    what makes a plain "open a session and type hi" produce a usable Standard
    reference; treating them as unknown would silently discard it.
    """
    try:
        events = unpack(read_events(session_dir))
    except Exception:
        return None
    preset, header = None, None
    for ev in events:
        kind, data = ev.get("type"), ev.get("data") or {}
        if kind == "agent-preset/selected":
            preset = data.get("agentPreset")
        elif kind == "request/header" and header is None:
            header = data.get("header") or {}
    if header is None:
        return None
    return {
        "dir": session_dir,
        "preset": preset or default_preset,
        "explicit_preset": preset is not None,
        "tools": len(header.get("tools") or []),
        "system_chars": len(header.get("system") or ""),
        "model": (header.get("config") or {}).get("model"),
    }


# What each shipped preset's catalog must look like when nothing else is in the
# tree. A reference that does not match was captured from a contaminated
# profile -- almost always the daily-driver `web` profile, whose plugins add 9
# tools -- and using it would move the gate's goalposts instead of testing.
EXPECTED_CATALOG = {"minimal": 2, "standard": 25}


# The testbed's own cell runs live under these workspaces. They must never be
# considered as REFERENCES: a headless cell mounts no preset roster at all, so
# it emits no `agent-preset/selected` event and the default-preset attribution
# would file every one of them under `standard` -- and they are newer than the
# real reference, so newest-per-preset would pick a cell as its own baseline.
CELL_WORKSPACE_MARKER = "dsh-ablation-workspaces"


def find_candidates(
    workspace_filter: str = "",
    default_preset: str = "standard",
    exclude: str | None = None,
) -> list[dict]:
    """Every session under a workspace whose encoded name contains the filter.

    The filter defaults to everything: the Web UI picks its own workspace, which
    need not be the directory the server was launched from, so keying discovery
    off the launch cwd finds nothing. Newest-per-preset wins instead.
    """
    found = []
    for ws in SESSIONS.iterdir():
        if not ws.is_dir() or workspace_filter not in ws.name:
            continue
        if exclude and exclude in ws.name:
            continue
        for sd in ws.iterdir():
            if not sd.is_dir():
                continue
            info = describe(sd, default_preset)
            if info:
                info["mtime"] = sd.stat().st_mtime
                found.append(info)
    return sorted(found, key=lambda i: i["mtime"])


def newest_for_preset(candidates: list[dict], preset: str) -> dict | None:
    matches = [c for c in candidates if c["preset"] == preset]
    return matches[-1] if matches else None


def gate(reference: Path, candidate: Path, label: str) -> bool:
    """Run the header-equivalence check for one cell and report the verdict."""
    cmd = [
        sys.executable, str(ROOT / "bin" / "check_header_equivalence.py"),
        str(reference), str(candidate),
    ]
    for delta in ACCEPTED_DELTAS:
        cmd += ["--accept-delta", delta]
    print(f"\n{'=' * 78}\n== {label}\n{'=' * 78}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip())
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="",
                        help="optional substring filter on the encoded workspace dir name")
    parser.add_argument("--default-preset", default="standard",
                        help="preset attributed to sessions with no explicit selection event")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    candidates = find_candidates(
        args.workspace, args.default_preset, exclude=CELL_WORKSPACE_MARKER
    )
    if not candidates:
        print("no sessions with a request/header found.")
        print("Start the reference server and send one message under each preset first.")
        return 1

    print("sessions found (newest last):\n")
    print(f"{'preset':<12}{'expl':>5}{'tools':>6}{'sysChars':>10}{'model':>20}  dir")
    for c in candidates[-12:]:
        print(f"{str(c['preset']):<12}{'yes' if c['explicit_preset'] else '-':>5}"
              f"{c['tools']:>6}{c['system_chars']:>10}"
              f"{str(c['model']):>20}  {c['dir'].name}")

    if args.list:
        return 0

    REFERENCE.mkdir(exist_ok=True)
    pairs = [("minimal", "b0-minimal"), ("standard", "b1-standard")]
    manifest, ok_all, missing = {}, True, []

    for preset, cell in pairs:
        ref = newest_for_preset(candidates, preset)
        if ref is None:
            missing.append(preset)
            continue

        expected = EXPECTED_CATALOG.get(preset)
        if expected is not None and ref["tools"] != expected:
            print(f"\nREJECTED reference for {preset!r}: {ref['tools']} tools, expected "
                  f"{expected} ({ref['dir'].name}).")
            print("  A shipped preset with the wrong catalog size was captured from a "
                  "contaminated profile.\n  Re-capture it from a profile holding only "
                  "dsh-base + dsh-web-app.")
            ok_all = False
            continue

        cand_dirs = find_candidates(f"verify-{cell}", args.default_preset)
        if not cand_dirs:
            print(f"\nno ablation session for cell {cell!r}; run that cell once first.")
            ok_all = False
            continue
        cand = cand_dirs[-1]
        manifest[preset] = {
            "reference_session": str(ref["dir"]),
            "reference_tools": ref["tools"],
            "reference_system_chars": ref["system_chars"],
            "cell": cell,
            "cell_session": str(cand["dir"]),
            "cell_tools": cand["tools"],
            "cell_system_chars": cand["system_chars"],
        }
        passed = gate(ref["dir"], cand["dir"], f"{preset}  vs  {cell}")
        manifest[preset]["gate_passed"] = passed
        ok_all = ok_all and passed

    if missing:
        print(f"\nNo reference session for preset(s): {missing}.")
        print("Open the reference server, pick that preset in a NEW session, send one message.")
        ok_all = False

    if manifest:
        (REFERENCE / "reference-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {REFERENCE / 'reference-manifest.json'}")

    print("\n" + ("ADMISSION GATE PASSED — the matrix may run."
                  if ok_all else
                  "ADMISSION GATE NOT PASSED — do not trust matrix labels yet."))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
