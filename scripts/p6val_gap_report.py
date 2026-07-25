#!/usr/bin/env python
"""Score a p6 run as a PLAN-vs-BEHAVIOUR GAP measure, not a vote predictor.

The policy-first design decouples hallning (what the party's PLAN implies) from
rost (how the party actually voted). The product is the GAP: cases where the
plan points one way and the party voted the other. So "vote accuracy" is the
wrong yardstick — a divergence is the signal, not an error.

But a gap is only signal if the plan-reading is trustworthy. A model with a lazy
prior ("V opposes everything") produces a fake gap: the cases it got 'wrong' are
just where the prior missed, not where the party defied its programme. The
internal-validity test is whether the model's OWN uncertainty signals
(coverage, plan_tacker_utskottets_skal, confidence) separate aligned cases from
gap cases. If gaps concentrate where the model itself said the plan is silent or
doesn't reach the committee's reasoning, the gap detector is coherent. If gaps
are spread evenly across confident and unconfident reads, the 'gap' is noise.

  uv run python scripts/p6val_gap_report.py V2024
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from aidag.promptgen import HALLNING_TO_ROST  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    # ARGS: [context] [out-dir] — out-dir defaults to "out", and the manifest is
    # matched to it ("out60" -> manifest60.json) so an extended case set scores
    # against its own manifest rather than the 48-case original.
    name = sys.argv[1] if len(sys.argv) > 1 else "V2024"
    sub = sys.argv[2] if len(sys.argv) > 2 else "out"
    d = ROOT / "data" / "interim" / "p6val" / name
    # "out60" -> manifest60.json, but only if it exists: "out2" is a second pass
    # over the ORIGINAL set, not a 2-case set, and must keep manifest.json.
    mf = (f"manifest{sub[3:]}.json"
          if sub[3:].isdigit() and (d / f"manifest{sub[3:]}.json").exists()
          else "manifest.json")
    man = {m["cid"]: m for m in json.loads((d / mf).read_text())}
    got = {}
    for line in (d / sub / "decisions.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            got[str(r["cid"])] = r
    party = man["000"]["parti"]
    print(f"{name} ({party}): {len(got)} decisions — scored as plan-vs-behaviour gap\n")

    # A decision is part of the gap analysis only where the plan actually speaks.
    jn = [i for i in got if man[i]["actual"] in ("Ja", "Nej")]
    speaks = [i for i in jn if got[i]["coverage"] != "not_covered"]
    gap = [i for i in speaks if HALLNING_TO_ROST[got[i]["hallning"]] != man[i]["actual"]]
    aligned = [i for i in speaks if i not in gap]

    print("=== the gap product ===")
    print(f"  plan speaks on {len(speaks)}/{len(jn)} Ja/Nej cases "
          f"({len(jn)-len(speaks)} not_covered, excluded)")
    print(f"  ALIGNED (plan-implied vote == actual): {len(aligned)}/{len(speaks)} "
          f"({len(aligned)/len(speaks):.0%})")
    print(f"  GAP     (party voted against its plan): {len(gap)}/{len(speaks)} "
          f"({len(gap)/len(speaks):.0%})  <- the reportable signal")

    # direction: when the plan says oppose (Nej) but they voted Ja = 'voted WITH
    # the committee despite the programme'; the reverse = 'defected against it'.
    dir_ = collections.Counter(
        ("plan→Nej, voted Ja" if HALLNING_TO_ROST[got[i]["hallning"]] == "Nej"
         else "plan→Ja, voted Nej") for i in gap)
    print("  gap direction:", dict(dir_))

    # ---- INTERNAL VALIDITY: does the model's own uncertainty predict the gap? --
    print("\n=== is the gap real signal or a lazy prior? ===")
    print("  (if gaps concentrate where the model flags weak coverage, it's reading")
    print("   the case; if gaps are even across confident/unconfident, it's noise)\n")
    for field, getter in (
        ("coverage", lambda r: r["coverage"]),
        ("confidence", lambda r: r["confidence"]),
        ("plan_tacker_utskottets_skal", lambda r: r.get("plan_tacker_utskottets_skal")),
    ):
        print(f"  by {field}:")
        levels = sorted({getter(got[i]) for i in speaks}, key=str)
        for lv in levels:
            ids = [i for i in speaks if getter(got[i]) == lv]
            g = sum(1 for i in ids if i in gap)
            print(f"    {str(lv):<12} n={len(ids):>3}  gap rate {g/len(ids):>4.0%}")
    # a coherent detector shows a MONOTONE gap rate: explicit<inferred,
    # high<low, plan_tacker=ja < plan_tacker=nej.

    # ---- the real abstentions: pure gap (party chose neither side) ------------
    ab = [i for i in got if man[i]["actual"] == "Avstår"]
    if ab:
        tak = collections.Counter(got[i].get("plan_tacker_utskottets_skal") for i in ab)
        cov = collections.Counter(got[i]["coverage"] for i in ab)
        print(f"\n=== real abstentions (n={len(ab)}) — party took neither side ===")
        print(f"  a plan-vs-behaviour detector should read these as low-coverage /")
        print(f"  plan-doesn't-cover more often than clear-stance cases do.")
        print(f"  coverage: {dict(cov)} | plan_tacker: {dict(tak)}")


if __name__ == "__main__":
    main()
