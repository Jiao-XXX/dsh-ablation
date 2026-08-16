#!/usr/bin/env python3
"""Admission gate: prove a headless ablation cell shows the model the same
request header as the real Web preset it claims to reproduce.

The whole testbed rests on one claim -- that `B0` IS Minimal and `B1` IS
Standard, only running headless. That claim is checkable rather than
assumable, because `request/header` records the rendered system prompt and the
assembled tool schemas verbatim in the durable log. This script compares a
candidate run's first header against a reference run's, and fails loudly on any
difference.

Run it BEFORE spending tokens on a matrix. A cell that fails here is measuring
something other than what its name says.

Usage:
    check_header_equivalence.py <reference-session> <candidate-session>
                               [--ignore-tool NAME ...] [--header-index N]

`--ignore-tool` drops a tool name from BOTH sides before comparing. Its only
legitimate use is excluding third-party plugin tools that pollute a reference
profile (e.g. a daily-driver Web profile carrying `vision_*` / `ssh_*`). Every
ignored name weakens the result from "identical catalog" to "identical core
catalog", so the script prints the downgrade in its verdict.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_metrics import read_events, unpack  # noqa: E402


def first_header(path: Path, index: int = 0) -> dict:
    """Return the `index`-th request/header payload from a session log."""
    headers = [
        ev.get("data") or {}
        for ev in unpack(read_events(path))
        if ev.get("type") == "request/header"
    ]
    if not headers:
        raise SystemExit(f"no request/header events in {path}")
    if index >= len(headers):
        raise SystemExit(
            f"{path} has {len(headers)} header(s); index {index} out of range"
        )
    return headers[index].get("header") or {}


_CWD_PATTERN = re.compile(r"working directory is (/[^\s,;]+?)\.?(?:\s|$)")


def workspace_cwd(system_text: str, override: str | None = None) -> str | None:
    """Recover the workspace path this request ran in, from the prompt itself.

    The standard persona interpolates `{{cwd}}` ("Your working directory is
    /path."), so two runs of the SAME preset in different workspaces have
    system prompts differing only by that path -- a byte comparison would
    report a difference that is not a composition difference.

    The path is read out of the prompt text rather than reconstructed from the
    sessions directory name: that name encodes `/` as `-` without escaping the
    hyphens already in the path, so decoding it is ambiguous for any workspace
    whose own directories contain hyphens.
    """
    if override:
        return override
    match = _CWD_PATTERN.search(system_text)
    return match.group(1) if match else None


def normalize_system(text: str, override: str | None = None) -> str:
    """Replace the run's own workspace path with a stable placeholder."""
    cwd = workspace_cwd(text, override)
    return text.replace(cwd, "{{cwd}}") if cwd else text


def canonical_tools(header: dict, ignore: set[str]) -> dict[str, str]:
    """Map tool name -> canonical JSON of its full schema, minus ignored names."""
    out = {}
    for tool in header.get("tools") or []:
        name = tool.get("name")
        if not name or name in ignore:
            continue
        out[name] = json.dumps(tool, sort_keys=True, ensure_ascii=False)
    return out


def compare(
    ref: dict,
    cand: dict,
    ignore: set[str],
    ref_cwd: str | None = None,
    cand_cwd: str | None = None,
    accept_deltas: tuple[str, ...] = (),
) -> tuple[bool, list[str]]:
    """Return (equivalent, human-readable findings)."""
    findings: list[str] = []
    ok = True

    ref_sys = normalize_system(ref.get("system") or "", ref_cwd)
    cand_sys = normalize_system(cand.get("system") or "", cand_cwd)
    if ref_sys != cand_sys:
        diff_lines = list(difflib.unified_diff(
            ref_sys.splitlines(), cand_sys.splitlines(),
            fromfile="reference/system", tofile="candidate/system", lineterm="", n=1,
        ))
        changed = [
            line for line in diff_lines
            if line[:1] in "+-" and not line.startswith(("+++", "---"))
            # A removed or added BLANK line is the paragraph separator that
            # travelled with the paragraph beside it. Requiring it to be
            # declared separately would mean declaring an empty pattern, which
            # would match every line and disable the gate. Substantive content
            # still has to be accounted for.
            and line[1:].strip()
        ]
        # A delta is only excusable if it was declared in advance. Every changed
        # line must be covered by an --accept-delta pattern; one uncovered line
        # fails the gate for the whole header.
        unaccounted = [
            line for line in changed
            if not any(pattern in line for pattern in accept_deltas)
        ]
        if unaccounted:
            ok = False
            findings.append(
                f"SYSTEM PROMPT DIFFERS  reference={len(ref_sys)}c candidate={len(cand_sys)}c"
                "  (workspace paths already normalized to {{cwd}})"
            )
            findings.extend("    " + line for line in diff_lines[:60])
        else:
            findings.append(
                f"system prompt differs ONLY in declared-accepted deltas "
                f"({len(changed)} line(s); reference={len(ref_sys)}c candidate={len(cand_sys)}c)"
            )
            findings.extend("    accepted: " + line for line in changed[:10])
    else:
        findings.append(f"system prompt identical ({len(ref_sys)} chars, cwd-normalized)")

    ref_tools = canonical_tools(ref, ignore)
    cand_tools = canonical_tools(cand, ignore)

    only_ref = sorted(set(ref_tools) - set(cand_tools))
    only_cand = sorted(set(cand_tools) - set(ref_tools))
    if only_ref or only_cand:
        ok = False
        if only_ref:
            findings.append(f"TOOLS MISSING FROM CANDIDATE  {only_ref}")
        if only_cand:
            findings.append(f"TOOLS EXTRA IN CANDIDATE      {only_cand}")
    else:
        findings.append(f"tool name set identical ({len(ref_tools)} tools)")

    schema_mismatches = [
        name for name in sorted(set(ref_tools) & set(cand_tools))
        if ref_tools[name] != cand_tools[name]
    ]
    if schema_mismatches:
        ok = False
        findings.append(f"TOOL SCHEMAS DIFFER           {schema_mismatches}")
        for name in schema_mismatches[:3]:
            diff = difflib.unified_diff(
                json.dumps(json.loads(ref_tools[name]), indent=2, sort_keys=True).splitlines(),
                json.dumps(json.loads(cand_tools[name]), indent=2, sort_keys=True).splitlines(),
                fromfile=f"reference/{name}", tofile=f"candidate/{name}", lineterm="", n=1,
            )
            findings.extend("    " + line for line in list(diff)[:30])
    elif ref_tools:
        findings.append("tool schemas identical")

    # Route and sampling belong to the header too: the same catalog under a
    # different model or reasoning effort is a different experiment.
    ref_cfg = ref.get("config") or {}
    cand_cfg = cand.get("config") or {}
    for key in ("provider", "model", "reasoningEffort", "maxTokens"):
        if ref_cfg.get(key) != cand_cfg.get(key):
            ok = False
            findings.append(
                f"CONFIG DIFFERS  {key}: reference={ref_cfg.get(key)!r} "
                f"candidate={cand_cfg.get(key)!r}"
            )

    return ok, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="the Web-preset session to match")
    parser.add_argument("candidate", type=Path, help="the headless cell session")
    parser.add_argument(
        "--ignore-tool", action="append", default=[], metavar="NAME",
        help="drop this tool name from both sides before comparing (repeatable)",
    )
    parser.add_argument("--header-index", type=int, default=0)
    parser.add_argument(
        "--ref-cwd", help="override the reference workspace path (default: read from the prompt)"
    )
    parser.add_argument(
        "--cand-cwd", help="override the candidate workspace path (default: read from the prompt)"
    )
    parser.add_argument(
        "--accept-delta", action="append", default=[], metavar="SUBSTRING",
        help="declare a system-prompt difference as expected-by-design; every changed "
             "diff line must match one of these or the gate fails (repeatable)",
    )
    args = parser.parse_args()

    ignore = set(args.ignore_tool)
    ref = first_header(args.reference, args.header_index)
    cand = first_header(args.candidate, args.header_index)
    ok, findings = compare(
        ref, cand, ignore, args.ref_cwd, args.cand_cwd, tuple(args.accept_delta)
    )

    print(f"reference  {args.reference}")
    print(f"candidate  {args.candidate}")
    if ignore:
        print(f"ignored    {sorted(ignore)}")
    print()
    for line in findings:
        print(line)
    print()

    if ok and ignore:
        print("VERDICT: EQUIVALENT (core catalog only -- ignored tools were excluded)")
    elif ok:
        print("VERDICT: EQUIVALENT")
    else:
        print("VERDICT: NOT EQUIVALENT -- fix the overlay before running the matrix")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
