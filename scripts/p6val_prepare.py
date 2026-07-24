#!/usr/bin/env python
"""Build a stratified, mixed-outcome validation sample for the p6 grouped config.

All prior tuning (grouping, prompt, chunk, model) was measured on party M, which
votes Ja 2530/2539 — so it could measure citation fidelity and self-consistency
but NEVER vote accuracy. This builds the missing test: a single-context sample
whose real votes actually vary, so `hallning`-derived votes can be scored against
reality before the config is committed to all 20,312 decisions.

  uv run python scripts/p6val_prepare.py V 2024 --ja 20 --nej 20 --avst 8

Writes data/interim/p6val/<party><year>/: system/<party>.txt, group.json,
manifest.json (cid -> votering_id + real vote), out/. The manifest carries the
real vote so scoring never needs to re-join. Same file shapes as p6group, so the
grouped workflow and agent_meter run against it unchanged.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import polars as pl  # noqa: E402

from aidag.config import PROCESSED_DIR  # noqa: E402
from aidag.corpus import program_at  # noqa: E402
from aidag.promptgen import build_system_blocks, render_user_message  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "interim" / "p6val"
VERSION, ARM = "p6", "anonymous"


def spread(pool: list, k: int) -> list:
    """Deterministic even spread across a sorted pool (no RNG: reproducible)."""
    if not pool or k <= 0:
        return []
    step = max(1, len(pool) // k)
    return pool[::step][:k]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("party")
    ap.add_argument("prog_year", help="programme vintage, e.g. 2024")
    ap.add_argument("--ja", type=int, default=20)
    ap.add_argument("--nej", type=int, default=20)
    ap.add_argument("--avst", type=int, default=8)
    a = ap.parse_args()

    cases = {c["votering_id"]: c for c in
             pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)}
    pos = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")

    by_outcome: dict[str, list[str]] = collections.defaultdict(list)
    for r in pos.iter_rows(named=True):
        if r["parti"] != a.party or r["position"] == "Frånvarande":
            continue
        c = cases.get(r["votering_id"])
        if not c:
            continue
        prog = program_at(a.party, c["datum"])
        if not prog or prog["from"][:4] != a.prog_year:
            continue
        by_outcome[r["position"]].append(r["votering_id"])
    for v in by_outcome.values():
        v.sort(key=lambda vid: (cases[vid]["datum"], vid))

    picked: list[tuple[str, str]] = []  # (votering_id, real_vote)
    for outcome, k in (("Ja", a.ja), ("Nej", a.nej), ("Avstår", a.avst)):
        chosen = spread(by_outcome.get(outcome, []), k)
        picked += [(vid, outcome) for vid in chosen]
        if len(chosen) < k:
            print(f"  WARN: only {len(chosen)} {outcome} available (wanted {k})")
    picked.sort(key=lambda t: (cases[t[0]]["datum"], t[0]))

    d = OUT / f"{a.party}{a.prog_year}"
    (d / "system").mkdir(parents=True, exist_ok=True)
    (d / "out").mkdir(exist_ok=True)

    rep = picked[0][0]
    sys_text = "\n\n".join(b["text"] for b in
                           build_system_blocks(a.party, cases[rep]["datum"],
                                               cases[rep]["rm"], rep, VERSION))
    (d / "system" / f"{a.party}.txt").write_text(sys_text, encoding="utf-8")

    # integrity: the system file must be byte-identical for EVERY picked case, or
    # they aren't really one context and can't share one grouped agent.
    for vid, _ in picked:
        t = "\n\n".join(b["text"] for b in
                        build_system_blocks(a.party, cases[vid]["datum"],
                                            cases[vid]["rm"], vid, VERSION))
        if t != sys_text:
            raise SystemExit(f"system file differs for {vid} — not a single context")

    group, man = [], []
    for i, (vid, real) in enumerate(picked):
        cid = f"{i:03d}"
        group.append({"cid": cid, "user": render_user_message(cases[vid], ARM, VERSION)})
        man.append({"cid": cid, "votering_id": vid, "parti": a.party,
                    "actual": real, "datum": cases[vid]["datum"]})
    (d / "group.json").write_text(json.dumps({"cases": group}, ensure_ascii=False),
                                  encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    # copy the p6 schema next to it so the workflow's schema path is local
    (d / "schema.json").write_text(
        (ROOT / "data/interim/p6group/schema.json").read_text(), encoding="utf-8")

    print(f"{a.party} {a.prog_year}: {len(picked)} cases -> {d}")
    print("  outcomes:", dict(collections.Counter(r for _, r in picked)))
    print(f"  system {len(sys_text)//4}k tok | scorable (Ja/Nej): "
          f"{sum(1 for _,r in picked if r!='Avstår')}")


if __name__ == "__main__":
    main()
