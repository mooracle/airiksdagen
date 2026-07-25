#!/usr/bin/env python
"""Prepare the p6 schema-gate sample (spec §5 step 4).

Writes one system file per (party, context) and one request file per decision
into data/interim/p6sample/. A workflow then runs one Opus agent per request.

The sample is stratified to answer the three things the gate must settle:
  1. does `ingendera` fire where the party really stood outside the pair?
     -> oversample decisions whose REAL vote was Avstår
  2. does `explicit` coverage rise once the committee's reasoning is in context?
     -> only (case, party) pairs that full-v3 already decided, so p5 is a paired
        baseline on identical inputs
  3. do citations stay verbatim-checkable against the narrowed corpus?
     -> checked after the run, against the same documents the agent was given
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import polars as pl  # noqa: E402

from aidag.casemeta import load_casemeta  # noqa: E402
from aidag.config import PARTIES, PROCESSED_DIR  # noqa: E402
from aidag.corpus import context_key  # noqa: E402
from aidag.promptgen import (  # noqa: E402
    build_system_blocks,
    decision_schema,
    render_user_message,
)

VERSION = "p6"
PER_PARTY_ABSTAIN = 3
PER_PARTY_OTHER = 5
OUT = Path(__file__).resolve().parents[1] / "data" / "interim" / "p6sample"


def main() -> None:
    cases = {c["votering_id"]: c for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)}
    pos = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual = {(r["votering_id"], r["parti"]): r["position"] for r in pos.iter_rows(named=True)}
    cm = load_casemeta()

    v3: dict[tuple[str, str], dict] = {}
    for p in (Path(__file__).resolve().parents[1] / "data/results/simulations/full-v3").glob("*.jsonl"):
        for line in p.open():
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            v3[(r["votering_id"], r["parti"])] = r

    # candidates: decided in full-v3, has a casemeta brief, real vote comparable
    cands: dict[str, dict[str, list]] = {c: {"Avstår": [], "other": []} for c in PARTIES}
    for (vid, party), rec in sorted(v3.items()):
        a = actual.get((vid, party))
        if a is None or a == "Frånvarande" or vid not in cases:
            continue
        ag = (cm.get(vid) or {}).get("agent") or {}
        if not (ag.get("committee") or {}).get("sv") or not (ag.get("alternatives") or []):
            continue
        cands[party]["Avstår" if a == "Avstår" else "other"].append((vid, party, a, rec))

    picked = []
    for party in PARTIES:
        for bucket, k in (("Avstår", PER_PARTY_ABSTAIN), ("other", PER_PARTY_OTHER)):
            pool = cands[party][bucket]
            # deterministic spread across the pool (no RNG: reproducible)
            step = max(1, len(pool) // k) if pool else 1
            picked += pool[::step][:k]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "system").mkdir(exist_ok=True)
    (OUT / "req").mkdir(exist_ok=True)
    (OUT / "out").mkdir(exist_ok=True)

    schema = decision_schema(VERSION)
    manifest = []
    seen_sys: set[str] = set()
    for i, (vid, party, a, v3rec) in enumerate(picked):
        case = cases[vid]
        ck = context_key(party, case["datum"], case["rm"], vid, VERSION)
        sysname = f"{party}-{ck}.txt"
        if sysname not in seen_sys:
            blocks = build_system_blocks(party, case["datum"], case["rm"], vid, VERSION)
            (OUT / "system" / sysname).write_text(
                "\n\n".join(b["text"] for b in blocks), encoding="utf-8"
            )
            seen_sys.add(sysname)
        req = {
            "id": f"{i:03d}",
            "votering_id": vid,
            "parti": party,
            "system_file": str((OUT / "system" / sysname).resolve()),
            "user_message": render_user_message(case, "anonymous", VERSION),
            "schema": schema,
            "out_file": str((OUT / "out" / f"{i:03d}.json").resolve()),
        }
        (OUT / "req" / f"{i:03d}.json").write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        manifest.append({"id": f"{i:03d}", "votering_id": vid, "parti": party, "actual": a,
                         "v3_rost": v3rec["rost"], "v3_coverage": v3rec["coverage"],
                         "v3_confidence": v3rec["confidence"]})
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    import collections
    print(f"p6 sample: {len(manifest)} decisions, {len(seen_sys)} distinct party contexts")
    print("  by party :", dict(collections.Counter(m["parti"] for m in manifest)))
    print("  real vote:", dict(collections.Counter(m["actual"] for m in manifest)))
    print("  v3 coverage:", dict(collections.Counter(m["v3_coverage"] for m in manifest)))
    print(f"  -> {OUT}")


if __name__ == "__main__":
    main()
