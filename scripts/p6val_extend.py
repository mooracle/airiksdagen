#!/usr/bin/env python
"""Extend a p6val validation set to N cases, keeping the existing ones as a prefix.

  uv run python scripts/p6val_extend.py V 2024 60

`p6group_extend.py` does this for the party-M grouping probe, but that set is
unstratified (M votes Ja on everything, so there is nothing to balance) and its
manifest uses a different shape. A p6val set is chosen for mixed outcomes and
its manifest carries the real vote, so extending one has two extra obligations:

  * keep the Ja/Nej/Avstår proportions of the original sample, otherwise the
    added cases quietly re-weight the accuracy denominator;
  * keep every existing cid, so arms already run on the 48-case set still score
    case-for-case against an arm run on the extended set.

Writes group<N>.json / manifest<N>.json / out<N>/ alongside the originals and
leaves group.json / manifest.json untouched — the two reproducibility passes in
out/ and out2/ stay valid.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import polars as pl  # noqa: E402

from aidag.config import PROCESSED_DIR  # noqa: E402
from aidag.corpus import program_at  # noqa: E402
from aidag.promptgen import build_system_blocks, render_user_message  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "interim" / "p6val"
VERSION, ARM = "p6", "anonymous"
OUTCOMES = ("Ja", "Nej", "Avstår")


def spread(pool: list, k: int) -> list:
    """Deterministic even spread across a sorted pool (no RNG: reproducible)."""
    if not pool or k <= 0:
        return []
    step = max(1, len(pool) // k)
    return pool[::step][:k]


def targets(have: collections.Counter, total: int) -> dict[str, int]:
    """Split `total` across outcomes in the proportions already present.

    Largest-remainder, then clamped so no outcome is asked to shrink — the
    existing cases are a fixed prefix and cannot be removed.
    """
    n0 = sum(have.values())
    exact = {o: have[o] * total / n0 for o in OUTCOMES}
    out = {o: int(exact[o]) for o in OUTCOMES}
    for o in sorted(OUTCOMES, key=lambda o: -(exact[o] - int(exact[o])))[: total - sum(out.values())]:
        out[o] += 1
    for o in OUTCOMES:  # never below what we already hold
        out[o] = max(out[o], have[o])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("party")
    ap.add_argument("prog_year")
    ap.add_argument("target", type=int)
    a = ap.parse_args()

    d = OUT / f"{a.party}{a.prog_year}"
    base_cases = json.loads((d / "group.json").read_text())["cases"]
    base_man = json.loads((d / "manifest.json").read_text())
    if a.target <= len(base_cases):
        raise SystemExit(f"already have {len(base_cases)} cases; nothing to add")

    used = {m["votering_id"] for m in base_man}
    have = collections.Counter(m["actual"] for m in base_man)
    want = targets(have, a.target)
    need = {o: want[o] - have[o] for o in OUTCOMES}

    cases = {c["votering_id"]: c for c in
             pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)}
    pos = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")

    # candidate pool: same party, same programme vintage, not already used
    by_outcome: dict[str, list[str]] = collections.defaultdict(list)
    for r in pos.iter_rows(named=True):
        if r["parti"] != a.party or r["position"] == "Frånvarande" or r["votering_id"] in used:
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

    picked: list[tuple[str, str]] = []
    for o in OUTCOMES:
        chosen = spread(by_outcome.get(o, []), need[o])
        if len(chosen) < need[o]:
            print(f"  WARN: only {len(chosen)} more {o} available (wanted {need[o]})")
        picked += [(vid, o) for vid in chosen]
    picked.sort(key=lambda t: (cases[t[0]]["datum"], t[0]))

    # integrity: every added case must render the SAME system file as the set
    # already on disk, or it is a different context and cannot share one agent
    sys_text = (d / "system" / f"{a.party}.txt").read_text(encoding="utf-8")
    for vid, _ in picked:
        t = "\n\n".join(b["text"] for b in
                        build_system_blocks(a.party, cases[vid]["datum"],
                                            cases[vid]["rm"], vid, VERSION))
        if t != sys_text:
            raise SystemExit(f"system file differs for {vid} — not the same context")

    out_cases = list(base_cases)
    out_man = list(base_man)
    for k, (vid, real) in enumerate(picked):
        cid = f"{len(base_cases) + k:03d}"
        out_cases.append({"cid": cid,
                          "user": render_user_message(cases[vid], ARM, VERSION)})
        out_man.append({"cid": cid, "votering_id": vid, "parti": a.party,
                        "actual": real, "datum": cases[vid]["datum"]})

    n = len(out_cases)
    (d / f"group{n}.json").write_text(json.dumps({"cases": out_cases}, ensure_ascii=False),
                                      encoding="utf-8")
    (d / f"manifest{n}.json").write_text(json.dumps(out_man, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    (d / f"out{n}").mkdir(exist_ok=True)

    mix = collections.Counter(m["actual"] for m in out_man)
    dts = sorted(m["datum"] for m in out_man)
    print(f"{a.party} {a.prog_year}: group{n}.json = {n} cases "
          f"(kept {len(base_cases)}, added {len(picked)})")
    print(f"  outcomes {dict(mix)}  (was {dict(have)})")
    print(f"  scorable (Ja/Nej): {mix['Ja'] + mix['Nej']}")
    print(f"  dates {dts[0]} -> {dts[-1]}")
    print(f"  out dir: {d / f'out{n}'}")


if __name__ == "__main__":
    main()
