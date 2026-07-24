#!/usr/bin/env python
"""Wall time + token cost per agent, read back from Claude Code transcripts.

The workflow harness writes one JSONL transcript per subagent under
  ~/.claude/projects/<proj>/<session>/subagents/workflows/wf_*/agent-*.jsonl
Every assistant line carries a timestamp and `message.usage`, so a finished run
can be metered after the fact — no instrumentation, no re-run.

Two things need care:
  * streaming emits several partial usage records per message; only the record
    with the largest output_tokens for a given message id is the real one.
  * an agent's wall time is last_ts - first_ts. Arm wall time is NOT the sum of
    those: agents inside one `parallel()` overlap, so the wall clock an operator
    actually waits is max(end) - min(start) across the arm.

Usage:
  uv run python scripts/agent_meter.py p6group/req=individual \\
      p6group/group.json=grouped p6group/seq=sequential
  uv run python scripts/agent_meter.py --list          # what runs exist
Each ARM is `substring=label`; an agent joins the first arm whose substring
appears in its opening prompt.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"

# Standard per-MTok prices. Cache writes cost 1.25x (5m ttl) or 2x (1h) of the
# input price; cache reads cost 0.1x.
PRICES = {  # model substring -> (input, output)
    "opus": (5.00, 25.00),
    "sonnet": (3.00, 15.00),
    "haiku": (1.00, 5.00),
}


def price_for(model: str) -> tuple[float, float]:
    for key, p in PRICES.items():
        if key in (model or ""):
            return p
    return (0.0, 0.0)


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def read_agent(path: Path) -> dict | None:
    """One agent transcript -> {prompt, model, wall, tokens...}."""
    try:
        lines = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001 — a truncated transcript is not fatal
        return None
    if not lines:
        return None

    first = lines[0].get("message", {}).get("content")
    prompt = first if isinstance(first, str) else " ".join(
        b.get("text", "") for b in (first or []) if isinstance(b, dict)
    )

    # last usage record wins per message id (streaming writes partials first)
    best: dict[str, dict] = {}
    # tool calls are counted the same way: a streamed message is written several
    # times as blocks arrive, so keep the largest count per message id.
    tools: dict[str, int] = {}
    model = ""
    times: list[float] = []
    for ln in lines:
        if ts := ln.get("timestamp"):
            times.append(_ts(ts))
        if ln.get("type") != "assistant":
            continue
        msg = ln.get("message") or {}
        mid = msg.get("id") or ln.get("uuid")
        blocks = msg.get("content")
        if isinstance(blocks, list):
            n = sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use")
            tools[mid] = max(tools.get(mid, 0), n)
        usage = msg.get("usage")
        if not usage:
            continue
        model = msg.get("model") or model
        prev = best.get(mid)
        if prev is None or usage.get("output_tokens", 0) >= prev.get("output_tokens", 0):
            best[mid] = usage

    tot = {"input": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 0}
    for u in best.values():
        tot["input"] += u.get("input_tokens", 0)
        tot["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
        tot["output"] += u.get("output_tokens", 0)
        cc = u.get("cache_creation") or {}
        if cc:
            tot["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0) or 0
            tot["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0) or 0
        else:
            tot["cache_write_5m"] += u.get("cache_creation_input_tokens", 0) or 0

    return {
        "path": path,
        "prompt": prompt,
        "model": model,
        "turns": len(best),
        "tools": sum(tools.values()),
        "start": min(times) if times else 0.0,
        "end": max(times) if times else 0.0,
        "wall": (max(times) - min(times)) if times else 0.0,
        **tot,
    }


def cost(a: dict) -> float:
    pin, pout = price_for(a["model"])
    return (
        a["input"] * pin
        + a["cache_write_5m"] * pin * 1.25
        + a["cache_write_1h"] * pin * 2.0
        + a["cache_read"] * pin * 0.1
        + a["output"] * pout
    ) / 1e6


def all_agents(project: str) -> list[dict]:
    """Every subagent transcript in the project, deduped.

    A resumed session writes the SAME workflow dir under both session ids, so
    the same agent-<id>.jsonl appears twice. Key on (run, agent id) and keep
    the longer copy — a resumed session's copy can be truncated.
    """
    root = PROJECTS / project
    seen: dict[tuple[str, str], dict] = {}
    # Workflow subagents live under workflows/wf_*; agents spawned directly with
    # the Agent tool (and kept alive across SendMessage turns) sit one level up.
    dirs = [(wf, wf.name) for wf in sorted(root.glob("*/subagents/workflows/wf_*"))]
    dirs += [(d, "direct") for d in sorted(root.glob("*/subagents"))]
    for d, run in dirs:
        for f in sorted(d.glob("agent-*.jsonl")):
            if not (a := read_agent(f)):
                continue
            a["run"] = run
            key = (run, f.name)
            if key not in seen or a["turns"] > seen[key]["turns"]:
                seen[key] = a
    return list(seen.values())


def fmt_secs(s: float) -> str:
    return f"{int(s)//60}m{int(s)%60:02d}s" if s >= 60 else f"{s:.0f}s"


def report(arms: dict[str, list[dict]], denom: dict[str, int]) -> None:
    hdr = (f"{'arm':<14}{'agents':>7}{'dec':>6}{'wall':>9}{'serial':>9}{'wall/dec':>10}"
           f"{'turns':>7}{'tools':>7}{'tokens':>11}{'tok/dec':>9}{'cached%':>9}"
           f"{'$':>8}{'$/dec':>9}")
    print(hdr)
    print("-" * len(hdr))
    for label, ags in arms.items():
        if not ags:
            continue
        n = denom.get(label) or len(ags)
        wall = max(a["end"] for a in ags) - min(a["start"] for a in ags)
        serial = sum(a["wall"] for a in ags)
        billed = sum(a["input"] + a["cache_write_5m"] + a["cache_write_1h"]
                     + a["cache_read"] + a["output"] for a in ags)
        cached = sum(a["cache_read"] for a in ags)
        c = sum(cost(a) for a in ags)
        print(f"{label:<14}{len(ags):>7}{n:>6}{fmt_secs(wall):>9}{fmt_secs(serial):>9}"
              f"{fmt_secs(wall/n):>10}{sum(a['turns'] for a in ags):>7}"
              f"{sum(a['tools'] for a in ags):>7}"
              f"{billed/1000:>10.0f}k{billed/n/1000:>8.1f}k"
              f"{cached/billed:>8.0%}{c:>8.2f}{c/n:>9.3f}")
    print("\nwall   = max(end)-min(start): the clock an operator actually waits, with")
    print("         in-arm parallelism (the harness runs ~10 agents at once) included.")
    print("serial = sum of per-agent wall: the same work with no parallelism. This is")
    print("         what scales — `wall` shrinks only while spare concurrency exists.")
    print("tokens = all billed units (fresh input + cache writes + reads + output).")


def main() -> None:
    project = "-Users-mgyk-mooracle-aidag"
    args = [a for a in sys.argv[1:] if a != "--list"]
    agents = all_agents(project)

    if "--list" in sys.argv or not args:
        print(f"{len(agents)} agents across runs:\n")
        seen: dict[str, list[dict]] = {}
        for a in agents:
            seen.setdefault(a["run"], []).append(a)
        for run, ags in sorted(seen.items(), key=lambda kv: -max(x["end"] for x in kv[1])):
            when = datetime.fromtimestamp(max(x["end"] for x in ags)).strftime("%m-%d %H:%M")
            print(f"  {when}  {run}  {len(ags):>4} agents  "
                  f"{ags[0]['prompt'][:70].replace(chr(10), ' ')}")
        if not args:
            return

    # ARM is `substring=label` or `substring=label=decisions`. The explicit
    # decision count matters whenever agents != decisions — one grouped agent
    # writes 40 of them, and a stalled sequential agent writes fewer than asked.
    specs, denom = [], {}
    for raw in args:
        parts = raw.split("=")
        sub, label = parts[0], (parts[1] if len(parts) > 1 else parts[0])
        specs.append((sub, label))
        if len(parts) > 2:
            denom[label] = int(parts[2])
    arms: dict[str, list[dict]] = {label: [] for _, label in specs}
    for a in agents:
        for sub, label in specs:
            if sub in a["prompt"]:
                arms[label].append(a)
                break
    report(arms, denom)


if __name__ == "__main__":
    main()
