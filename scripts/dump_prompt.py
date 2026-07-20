#!/usr/bin/env python
"""Dump everything an agent receives for one case × party to Markdown.

Renders the exact simulation prompt — the system blocks (role + each party
document, by category) and the case-specific user message — plus the response
schema and, if present, the case's metadata-layer record. Read-only.

Usage (from repo root):
  uv run python scripts/dump_prompt.py <votering_id> <PARTY> [options]

  <votering_id>   full id or a unique prefix (case-insensitive)
  <PARTY>         S | M | SD | C | V | KD | MP | L

Options:
  --arm {anonymous,labeled}   reservation authorship scrub (default: anonymous)
  --prompt-version P          p4 | p5 (default: config.PROMPT_VERSION)
  --truncate N                cap each party document at N words (default: full)
  --out PATH                  output file (default: dump-<vid8>-<PARTY>.md)
  --stdout                    print to stdout instead of writing a file

Examples:
  uv run python scripts/dump_prompt.py 4AA83A71 SD
  uv run python scripts/dump_prompt.py 4AA83A71 SD --truncate 150 --stdout
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import polars as pl

from aidag.config import PARTIES, PROCESSED_DIR, PROMPT_VERSION
from aidag.promptgen import build_system_blocks, decision_schema, render_user_message


def find_case(vid: str) -> dict:
    df = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    hit = df.filter(pl.col("votering_id").str.to_uppercase().str.starts_with(vid.upper()))
    if hit.height == 0:
        sys.exit(f"no case matches votering_id prefix {vid!r}")
    if hit.height > 1:
        ids = ", ".join(hit["votering_id"].head(5).to_list())
        sys.exit(f"ambiguous prefix {vid!r} — matches {hit.height} cases: {ids} …")
    return hit.row(0, named=True)


def _fence(text: str) -> str:
    """Wrap in a code fence, escaping any ``` already inside."""
    if "```" in text:
        return "````\n" + text + "\n````"
    return "```\n" + text + "\n```"


def _truncate_words(text: str, limit: int | None) -> str:
    if not limit:
        return text
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + f"\n\n… [{len(words)} words total — truncated to {limit}] …"


def render_markdown(case: dict, party: str, arm: str, prompt_version: str, truncate: int | None) -> str:
    vid = case["votering_id"]
    blocks = build_system_blocks(
        party, case["datum"], rm=case["rm"], votering_id=vid, prompt_version=prompt_version
    )
    out: list[str] = []
    w = out.append

    w(f"# Prompt dump — {case['rubrik']}\n")
    w(f"- **Case:** `{vid}` · {case['rm']}:{case['beteckning']} · p.{case['punkt']}"
      f" · {case['datum']} · utskott **{case['utskott']}**")
    w(f"- **Party:** {party} ({PARTIES[party]['name']})")
    w(f"- **Arm:** {arm}  ·  **Prompt version:** {prompt_version}")
    w(f"- **System blocks:** {len(blocks)} (byte-identical & prompt-cached across all of {party}'s cases)\n")

    # --- case metadata layer (optional) ---
    try:
        from aidag.metadata import load_metadata

        rec = load_metadata().get(vid)
    except Exception:  # noqa: BLE001
        rec = None
    if rec:
        w("## Case metadata (metadata layer — NOT part of the agent prompt today)\n")
        w(f"- **type:** {rec.get('type')}  ·  **policy_area:** {rec.get('policy_area')}"
          f"  ·  **parties_involved:** {', '.join(rec.get('parties_involved') or []) or '—'}")
        subj, stake = rec.get("subject") or {}, rec.get("at_stake") or {}
        w(f"- **subject (sv):** {subj.get('sv','')}")
        w(f"- **at_stake (sv):** {stake.get('sv','')}")
        w(f"- **subtopics:** {', '.join(rec.get('subtopics') or [])}")
        ag = rec.get("agent") or {}
        w(f"- **agent view (party-blind):** {ag.get('subject','')} / {ag.get('at_stake','')}\n")

    # --- system prompt, block by block ---
    w("## System prompt\n")
    for i, b in enumerate(blocks):
        text = b["text"]
        words = len(text.split())
        cached = "  ·  ⟵ 1h cache breakpoint" if b.get("cache_control") else ""
        if i == 0:
            w(f"### Block {i} — Role / instructions ({words} words){cached}\n")
            w(_fence(text) + "\n")
        else:
            tag = re.match(r"<([^>\n]+)>", text)
            label = tag.group(1) if tag else "document"
            w(f"### Block {i} — Document `<{label}>` ({words} words){cached}\n")
            w(_fence(_truncate_words(text, truncate)) + "\n")

    # --- user message ---
    w(f"## User message (arm = {arm}) — the case-specific text\n")
    w(_fence(render_user_message(case, arm=arm)) + "\n")

    # --- response schema ---
    w("## Response schema (structured output the agent must return)\n")
    schema = decision_schema(prompt_version)
    w("```json\n" + json.dumps(schema, ensure_ascii=False, indent=2) + "\n```\n")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump one case × party agent prompt to Markdown.")
    ap.add_argument("votering_id", help="full votering_id or a unique prefix")
    ap.add_argument("party", help="party code: " + " ".join(PARTIES))
    ap.add_argument("--arm", choices=["anonymous", "labeled"], default="anonymous")
    ap.add_argument("--prompt-version", default=PROMPT_VERSION)
    ap.add_argument("--truncate", type=int, default=None, help="cap each document at N words")
    ap.add_argument("--out", default=None, help="output path (default: dump-<vid8>-<PARTY>.md)")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing a file")
    args = ap.parse_args()

    # accept either a code (M) or a full name (Moderaterna), case-insensitive
    by_name = {info["name"].lower(): code for code, info in PARTIES.items()}
    party = args.party.upper() if args.party.upper() in PARTIES else by_name.get(args.party.lower())
    if party not in PARTIES:
        sys.exit(f"unknown party {args.party!r} — expected a code ({', '.join(PARTIES)}) or party name")

    case = find_case(args.votering_id)
    md = render_markdown(case, party, args.arm, args.prompt_version, args.truncate)

    if args.stdout:
        print(md)
        return
    out = args.out or f"dump-{case['votering_id'][:8]}-{party}.md"
    with open(out, "w") as f:
        f.write(md)
    print(f"wrote {out}  ({len(md):,} chars)")


if __name__ == "__main__":
    main()
