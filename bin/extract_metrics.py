#!/usr/bin/env python3
"""Offline proxy-metric extractor for one dsh ablation cell run.

Reads a durable session log (`session.jsonl.zstd`) and emits the proxy metrics
the ablation matrix scores cells on. Everything here comes from the durable log
alone -- no harness patching, no live hooks -- so a run can be re-scored later
with a changed metric definition without re-spending tokens.

Two envelope shapes appear in the log:

  ordinary   {"type": T, "seq": n, "time": t, "data": {...}}
  packed run {"type": "text-chunks"|"reasoning-chunks"|"tool-call-chunks",
              "seq0": n, "time0": t,
              "data": {"turn", "step", "index", "dt": [...],
                       "texts"|"args": [...], "id"?, "name"?}}

The packed rows are how `dsh-session`'s chunk-row codec compresses long delta
runs; each member of `texts`/`args` is one original chunk, so a run of length k
stands for k events starting at `seq0`.

Usage:
    extract_metrics.py <session-dir-or-jsonl.zstd> [--json] [--raw-events]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Emitted by @deepseek-ai/dsh-compaction-tool-result-pruner when it removes the
# middle of an oversized tool result. Its presence in a replacement's text is
# the ground truth that a specific result was mutilated.
PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"

PACKED_TYPES = {"text-chunks", "reasoning-chunks", "tool-call-chunks"}


def dsh_home() -> Path:
    """The dsh state directory, honouring `$DSH_HOME`.

    dsh reads this variable itself, so a deployment that moves its state
    elsewhere would otherwise leave every tool here reading an empty
    `~/.dsh` and reporting "no sessions found" on a machine full of them.
    """
    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


def sessions_root() -> Path:
    """Where dsh keys session logs by workspace."""
    return dsh_home() / "sessions"


# ---------------------------------------------------------------- log reading


def read_events(path: Path) -> list[dict]:
    """Decode a session log to a list of raw JSON event objects, in log order."""
    if path.is_dir():
        candidates = sorted(path.glob("session.jsonl*"))
        if not candidates:
            raise SystemExit(f"no session.jsonl* under {path}")
        path = candidates[0]

    if path.suffix == ".zstd" or path.name.endswith(".jsonl.zstd"):
        if not shutil.which("zstd"):
            raise SystemExit("zstd binary not found; install it or pass a plain .jsonl")
        proc = subprocess.run(
            ["zstd", "-dc", str(path)], capture_output=True, check=True
        )
        text = proc.stdout.decode("utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn final line can happen if the process died mid-flush. Every
            # earlier line is still valid, so keep them rather than failing the
            # whole cell over a crash tail.
            continue
    return events


def unpack(events: list[dict]) -> list[dict]:
    """Expand packed chunk-run rows into one synthetic event per member.

    Reconstructed members carry `seq` and the reconstructed chunk so the rest
    of the extractor sees a single uniform shape. Times are not reconstructed:
    no metric here depends on per-chunk timing.
    """
    out: list[dict] = []
    for ev in events:
        kind = ev.get("type")
        if kind not in PACKED_TYPES:
            out.append(ev)
            continue

        data = ev.get("data") or {}
        members = data.get("args") if kind == "tool-call-chunks" else data.get("texts")
        if not isinstance(members, list):
            out.append(ev)
            continue

        seq0 = ev.get("seq0", 0)
        chunk_type = {
            "text-chunks": "text-delta",
            "reasoning-chunks": "reasoning-delta",
            "tool-call-chunks": "tool-call-delta",
        }[kind]

        for offset, member in enumerate(members):
            chunk = {"type": chunk_type, "index": data.get("index")}
            if kind == "tool-call-chunks":
                chunk["id"] = data.get("id")
                if "name" in data:
                    chunk["name"] = data["name"]
                chunk["argumentsDelta"] = member
            else:
                chunk["text"] = member
            out.append(
                {
                    "type": "assistant/chunk",
                    "seq": seq0 + offset,
                    "data": {
                        "turn": data.get("turn"),
                        "step": data.get("step"),
                        "chunk": chunk,
                    },
                }
            )
    return out


# ------------------------------------------------------------------- helpers


def text_of(content) -> str:
    """Concatenate the text blocks of a content-block list."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts)


def classify_opening(line: str) -> str:
    """Bucket an opening line into the trajectory styles the ablation compares.

    `we` is the Minimal-condition signature reported by dsh-anchored-standard;
    `let-me` / `lets` are the standard-like signatures. Anything else is
    `other`, kept distinct rather than folded into a bucket it did not earn.
    """
    stripped = line.strip().lstrip("#*_-> ").strip()
    low = stripped.lower()
    if low.startswith(("we need", "we should", "we have", "we'll", "we ", "we,")):
        return "we"
    if low.startswith(("let me", "let me,")):
        return "let-me"
    if low.startswith(("let's", "lets ", "let us")):
        return "lets"
    if low.startswith(("i'll", "i will", "i need", "i'm", "i am")):
        return "i"
    if low.startswith(("looking at", "first,", "first ", "okay", "ok,")):
        return "orienting"
    return "other"


def tally_line_openings(text: str) -> Counter:
    """Count the opening style of every non-empty line in a reasoning stream."""
    counts: Counter = Counter()
    for line in text.splitlines():
        if not line.strip():
            continue
        counts[classify_opening(line)] += 1
    return counts


# ------------------------------------------------------------------ extractor


def extract(events: list[dict]) -> dict:
    events = unpack(events)

    session_id = None
    preset = None
    permission_preset = None
    sandbox_mode = None

    headers: list[dict] = []
    request_contexts: list[dict] = []

    steps = 0
    turns = 0
    turn_end_reasons: Counter = Counter()

    tool_calls: Counter = Counter()
    tool_call_total = 0
    tool_errors = 0

    assistant_messages = 0
    visible_replies = 0
    first_visible_line = None
    reasoning_text_by_block: dict[tuple, list[str]] = defaultdict(list)
    reasoning_blocks = 0

    usage_input = 0
    usage_output = 0
    usage_reasoning = 0
    usage_cached = 0

    prune_events = 0
    prune_shadowed_tokens = 0
    compaction_summaries = 0
    compaction_shadowed_tokens = 0

    # tool/result content by seq, so a later prune replacement can be diffed
    # against the original it shadows.
    result_chars_by_seq: dict[int, int] = {}
    pruned_chars = 0
    pruned_results = 0

    for ev in events:
        kind = ev.get("type")
        data = ev.get("data") or {}
        seq = ev.get("seq")

        if kind == "session":
            # The `session` event carries null data in practice; identity comes
            # from the containing directory name instead (set by the caller).
            pass
        elif kind == "agent-preset/selected":
            preset = data.get("agentPreset")
        elif kind == "permission/preset":
            permission_preset = data.get("preset") or data.get("name")
        elif kind == "sandbox/mode":
            sandbox_mode = data.get("mode")

        elif kind == "request/header":
            header = data.get("header") or {}
            config = header.get("config") or {}
            system = header.get("system") or ""
            tools = header.get("tools") or []
            headers.append(
                {
                    "seq": seq,
                    "reason": data.get("reason"),
                    "provider": config.get("provider"),
                    "model": config.get("model"),
                    "reasoningEffort": config.get("reasoningEffort"),
                    "maxTokens": config.get("maxTokens"),
                    "system_chars": len(system),
                    "system_sha256": hashlib.sha256(system.encode()).hexdigest()[:16],
                    "tool_count": len(tools),
                    "tool_names": sorted(t.get("name") for t in tools if t.get("name")),
                }
            )
        elif kind == "request/context":
            request_contexts.append(
                {
                    "provider": data.get("provider"),
                    "model": data.get("model"),
                    "contextWindow": data.get("contextWindow"),
                }
            )

        elif kind == "turn/start":
            turns += 1
        elif kind == "turn/end":
            reason = data.get("reason")
            if isinstance(reason, dict):
                reason = reason.get("kind", "unknown")
            turn_end_reasons[str(reason)] += 1
        elif kind == "step/start":
            steps += 1

        elif kind == "assistant/chunk":
            chunk = data.get("chunk") or {}
            ctype = chunk.get("type")
            if ctype == "reasoning-delta":
                key = (data.get("turn"), data.get("step"), chunk.get("index"))
                reasoning_text_by_block[key].append(chunk.get("text") or "")
            elif ctype == "block-start" and chunk.get("blockType") == "reasoning":
                reasoning_blocks += 1

        elif kind == "assistant/message":
            assistant_messages += 1
            message = data.get("message") or {}
            body = text_of(message.get("content"))
            if body.strip():
                visible_replies += 1
                if first_visible_line is None:
                    for line in body.splitlines():
                        if line.strip():
                            first_visible_line = line.strip()
                            break
            usage = data.get("usage") or {}
            usage_input += usage.get("inputTokens") or usage.get("promptTokens") or 0
            usage_output += usage.get("outputTokens") or usage.get("completionTokens") or 0
            usage_reasoning += usage.get("reasoningTokens") or 0
            usage_cached += usage.get("cachedInputTokens") or usage.get("cacheReadTokens") or 0

        elif kind == "tool/call":
            tool_call_total += 1
            tool_calls[str(data.get("name"))] += 1

        elif kind == "tool/result":
            body = text_of((data.get("message") or {}).get("content"))
            if seq is not None:
                result_chars_by_seq[seq] = len(body)
            if data.get("error"):
                tool_errors += 1
            if PRUNE_MARKER in body:
                pruned_results += 1

        elif kind == "compaction/prune":
            prune_events += 1
            prune_shadowed_tokens += data.get("shadowedTokenCount") or 0
        elif kind == "compaction/summary":
            compaction_summaries += 1
            compaction_shadowed_tokens += data.get("shadowedTokenCount") or 0

    reasoning_streams = {
        key: "".join(parts) for key, parts in reasoning_text_by_block.items()
    }
    all_reasoning = "\n".join(reasoning_streams.values())
    line_styles = tally_line_openings(all_reasoning)

    # Header churn is the anchoring signal: how many DISTINCT tool catalogs the
    # session presented, and at which request each change landed. A one-shot
    # anchor shows exactly two snapshots; a static cell shows one.
    catalogs = []
    for h in headers:
        sig = (h["tool_count"], tuple(h["tool_names"]))
        if not catalogs or catalogs[-1][0] != sig:
            catalogs.append((sig, h))

    return {
        "session_id": session_id,
        "agent_preset": preset,
        "permission_preset": permission_preset,
        "sandbox_mode": sandbox_mode,
        "headers": headers,
        "header_count": len(headers),
        "distinct_catalogs": len(catalogs),
        "catalog_sequence": [
            {"tool_count": h["tool_count"], "reason": h["reason"], "seq": h["seq"]}
            for _, h in catalogs
        ],
        "request_context": request_contexts[0] if request_contexts else None,
        "turns": turns,
        "steps": steps,
        "turn_end_reasons": dict(turn_end_reasons),
        "assistant_messages": assistant_messages,
        "visible_replies": visible_replies,
        "first_visible_line": first_visible_line,
        "first_line_style": classify_opening(first_visible_line or ""),
        "reasoning_blocks": max(reasoning_blocks, len(reasoning_streams)),
        "reasoning_chars": len(all_reasoning),
        "reasoning_line_styles": dict(line_styles),
        "tool_calls_total": tool_call_total,
        "tool_calls_by_name": dict(tool_calls),
        "tool_errors": tool_errors,
        "usage": {
            "input": usage_input,
            "output": usage_output,
            "reasoning": usage_reasoning,
            "cached_input": usage_cached,
        },
        "context_destruction": {
            "prune_events": prune_events,
            "prune_shadowed_tokens": prune_shadowed_tokens,
            "pruned_tool_results": pruned_results,
            "compaction_summaries": compaction_summaries,
            "compaction_shadowed_tokens": compaction_shadowed_tokens,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="session dir or session.jsonl.zstd")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument(
        "--raw-events", action="store_true", help="print a raw event-type histogram"
    )
    args = parser.parse_args()

    events = read_events(args.session)
    # `session-<uuid>` directory name is the only durable carrier of the id.
    dir_name = args.session.name if args.session.is_dir() else args.session.parent.name

    if args.raw_events:
        hist = Counter(e.get("type") for e in events)
        print(json.dumps(dict(hist.most_common()), indent=2, ensure_ascii=False))
        return 0

    metrics = extract(events)
    metrics["source"] = str(args.session)
    if not metrics.get("session_id") and dir_name.startswith("session-"):
        metrics["session_id"] = dir_name[len("session-"):]

    if args.json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
        return 0

    m = metrics
    print(f"session       {m['session_id']}   preset={m['agent_preset']}")
    print(f"catalogs      {m['distinct_catalogs']} distinct  ->  "
          f"{[c['tool_count'] for c in m['catalog_sequence']]}")
    for h in m["headers"]:
        print(f"  header seq={h['seq']} reason={h['reason']} "
              f"tools={h['tool_count']} system={h['system_chars']}c "
              f"model={h['model']} effort={h['reasoningEffort']}")
    print(f"turns/steps   {m['turns']} / {m['steps']}   end={m['turn_end_reasons']}")
    print(f"first line    [{m['first_line_style']}] {(m['first_visible_line'] or '')[:100]}")
    print(f"reasoning     {m['reasoning_blocks']} blocks, {m['reasoning_chars']}c, "
          f"styles={m['reasoning_line_styles']}")
    print(f"tool calls    {m['tool_calls_total']} (errors {m['tool_errors']}) "
          f"{m['tool_calls_by_name']}")
    print(f"usage         {m['usage']}")
    print(f"destruction   {m['context_destruction']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
