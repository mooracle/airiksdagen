"""Decision-by-decision comparison of two simulation runs (common cids).

Built for the grouped-vs-per-case execution experiment: run A is the
reference (per-case agents), run B the candidate (grouped agents). Reports
whether the execution design changes the votes, the coverage judgments, the
decisive citations, or agreement with the real outcome.
"""

from __future__ import annotations

import json

import polars as pl

from aidag.config import PARTY_CODES, PROCESSED_DIR, RESULTS_DIR
from aidag.simulate import _normalize_ws


def _load(run_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    sim_dir = RESULTS_DIR / "simulations" / run_id
    for path in sorted(sim_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            cid = f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"
            out[cid] = d
    return out


def run(run_a: str, run_b: str) -> None:
    a, b = _load(run_a), _load(run_b)
    common = sorted(set(a) & set(b))
    if not common:
        print("no common cids between the two runs")
        return

    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual = {
        (r["votering_id"], r["parti"]): r["position"] for r in positions.iter_rows(named=True)
    }

    n = len(common)
    rost_match = 0
    coverage_match = 0
    decisive_match = 0
    per_party = {p: [0, 0] for p in PARTY_CODES}  # match, total
    agree = {run_a: 0, run_b: 0}
    compared = 0
    avstar = {run_a: 0, run_b: 0}
    omvarld = {run_a: 0, run_b: 0}
    diffs = []
    for cid in common:
        da, db = a[cid], b[cid]
        parti, vid = da["parti"], da["votering_id"]
        same = da["rost"] == db["rost"]
        rost_match += same
        per_party[parti][0] += same
        per_party[parti][1] += 1
        coverage_match += da["coverage"] == db["coverage"]
        qa = _normalize_ws(da["citations"][0]["quote"]) if da.get("citations") else ""
        qb = _normalize_ws(db["citations"][0]["quote"]) if db.get("citations") else ""
        decisive_match += qa == qb
        for run_id, d in ((run_a, da), (run_b, db)):
            avstar[run_id] += d["rost"] == "Avstår"
            omvarld[run_id] += bool((d.get("omvarld") or {}).get("paverkar"))
        act = actual.get((vid, parti))
        if act and act != "Frånvarande":
            compared += 1
            agree[run_a] += da["rost"] == act
            agree[run_b] += db["rost"] == act
        if not same:
            diffs.append((cid, da["rost"], db["rost"], act or "?"))

    print(f"compare {run_a} (A) vs {run_b} (B): {n} common decisions")
    print(f"  rost identical:        {rost_match}/{n} ({rost_match / n:.1%})")
    print(f"  coverage identical:    {coverage_match}/{n} ({coverage_match / n:.1%})")
    print(f"  decisive quote same:   {decisive_match}/{n} ({decisive_match / n:.1%})")
    print(f"  agreement vs actual:   A {agree[run_a]}/{compared} ({agree[run_a] / compared:.1%})"
          f"  B {agree[run_b]}/{compared} ({agree[run_b] / compared:.1%})")
    print(f"  Avstår count:          A {avstar[run_a]}  B {avstar[run_b]}")
    print(f"  omvarld.paverkar:      A {omvarld[run_a]}  B {omvarld[run_b]}")
    print("  per-party rost match:  "
          + "  ".join(f"{p} {m}/{t}" for p, (m, t) in per_party.items() if t))
    if diffs:
        print(f"\n  {len(diffs)} differing decisions (A -> B, actual):")
        for cid, ra, rb, act in diffs[:30]:
            print(f"    {cid}: {ra} -> {rb} (actual {act})")
    only_a = len(set(a) - set(b))
    only_b = len(set(b) - set(a))
    if only_a or only_b:
        print(f"\n  cids only in A: {only_a}, only in B: {only_b}")
