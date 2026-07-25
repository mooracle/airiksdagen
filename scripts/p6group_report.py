#!/usr/bin/env python
"""Score the p6group grouping experiment: same 40 cases, three execution shapes.

  out_ind/  one fresh agent per case      (the baseline — no shared context)
  out_grp/  one agent decides all 40      (corpus read once, one long response)
  out_seq/  one agent, case-by-case turns (corpus read once, 40 turns)

All three were given the same system file and the same case texts, so any
difference is caused by the execution shape alone. The question this settles is
whether bundling costs citation quality: a citation is only real if its quote is
a verbatim substring of the document the agent was actually served.

Pair with `scripts/agent_meter.py` for the wall-time and token side.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import polars as pl  # noqa: E402

from aidag.config import PROCESSED_DIR  # noqa: E402
from aidag.promptgen import HALLNING_TO_ROST  # noqa: E402

G = Path(__file__).resolve().parents[1] / "data" / "interim" / "p6group"
REQ = {"hallning", "confidence", "coverage", "motivering", "citations", "omvarld",
       "flags", "plan_tacker_utskottets_skal"}


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def load_arm(name: str) -> dict[str, dict]:
    """-> {id: decision}. Individual is one file per case; the other two are JSONL."""
    d = G / name
    if not d.exists():
        return {}
    if name == "out_ind":
        out = {}
        for p in sorted(d.glob("*.json")):
            try:
                out[p.stem] = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                pass
        return out
    out = {}
    for p in sorted(d.glob("*.jsonl")):
        for line in p.open():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            # later files win: a restarted run rewrites earlier cids
            if cid := r.get("cid"):
                out[str(cid)] = r
    return out


def main() -> None:
    man = {m["id"]: m for m in json.loads((G / "manifest.json").read_text())}
    src = norm((G / "system" / "M.txt").read_text())
    pos = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual = {(r["votering_id"], r["parti"]): r["position"] for r in pos.iter_rows(named=True)}

    arms = {"individual": load_arm("out_ind"),
            "grouped-40": load_arm("out_grp"),
            "sequential": load_arm("out_seq")}

    print(f"p6group: {len(man)} cases (party M, one context), arms scored on identical inputs\n")
    hdr = (f"{'arm'::<14}" if False else f"{'arm':<13}{'done':>6}{'schema':>8}{'cites':>7}"
           f"{'cite/dec':>10}{'verbatim':>10}{'median q':>10}{'explicit':>10}{'agrees':>8}")
    print(hdr)
    print("-" * len(hdr))

    detail = {}
    for label, got in arms.items():
        if not got:
            continue
        n = len(got)
        bad_schema = sum(1 for r in got.values() if not REQ <= set(r))
        tot = ok = 0
        lens: list[int] = []
        misses: list[tuple[str, str]] = []
        for i, r in got.items():
            for c in r.get("citations") or []:
                tot += 1
                q = c.get("quote") or ""
                lens.append(len(q))
                if q and norm(q) in src:
                    ok += 1
                else:
                    misses.append((i, q[:70]))
        explicit = sum(1 for r in got.values() if r.get("coverage") == "explicit")
        # agreement against the real vote, excluding cases the party abstained on
        # (the two-valued hallning cannot express an abstention)
        cmp_ids = [i for i in got
                   if (a := actual.get((man[i]["votering_id"], man[i]["parti"]))) is not None
                   and a not in ("Avstår", "Frånvarande")]
        agree = sum(1 for i in cmp_ids
                    if HALLNING_TO_ROST.get(got[i].get("hallning")) ==
                    actual[(man[i]["votering_id"], man[i]["parti"])])
        med = sorted(lens)[len(lens) // 2] if lens else 0
        print(f"{label:<13}{n:>4}/{len(man):<1}{bad_schema:>8}{tot:>7}{tot/n:>10.2f}"
              f"{(ok/tot if tot else 0):>9.0%}{med:>9}c{explicit/n:>10.0%}"
              f"{(agree/len(cmp_ids) if cmp_ids else 0):>7.0%}")
        detail[label] = (misses, lens)

    print("\nverbatim = quote is an exact substring of the system file the agent was")
    print("served. A miss is an unusable citation: the claim cannot be checked.")
    print("agrees  = derived vote matches the real Riksdag vote (abstentions excluded).")

    for label, (misses, _lens) in detail.items():
        if misses:
            print(f"\n{label}: {len(misses)} unverifiable quotes, first few:")
            for i, q in misses[:5]:
                print(f"   [{i}] {q}")

    # Where the arms actually disagree on the vote itself
    common = set.intersection(*(set(a) for a in arms.values() if a))
    if len(arms) > 1 and common:
        base = arms["individual"]
        print(f"\nstance agreement vs individual baseline (n={len(common)} shared cases):")
        for label, got in arms.items():
            if label == "individual" or not got:
                continue
            same = sum(1 for i in common if got[i].get("hallning") == base[i].get("hallning"))
            print(f"  {label:<12} {same}/{len(common)} identical stance ({same/len(common):.0%})")
        cov = {label: collections.Counter(got[i].get("coverage") for i in common)
               for label, got in arms.items() if got}
        print("\n  coverage on those shared cases:")
        for label, c in cov.items():
            print(f"    {label:<12} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))

        # Agreement and citation density must be read on the SAME cases. The
        # headline table scores each arm on whatever it finished, so an arm that
        # stopped early is compared on a different (easier or harder) subset.
        shared = [i for i in common
                  if (a := actual.get((man[i]["votering_id"], man[i]["parti"]))) is not None
                  and a not in ("Avstår", "Frånvarande")]
        print(f"\n  on the same {len(shared)} comparable cases:")
        for label, got in arms.items():
            if not got:
                continue
            ag = sum(1 for i in shared
                     if HALLNING_TO_ROST.get(got[i].get("hallning")) ==
                     actual[(man[i]["votering_id"], man[i]["parti"])])
            cites = sum(len(got[i].get("citations") or []) for i in common)
            print(f"    {label:<12} agrees {ag}/{len(shared)} ({ag/len(shared):.0%})"
                  f"   {cites/len(common):.2f} citations/decision")


if __name__ == "__main__":
    main()
