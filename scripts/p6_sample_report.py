#!/usr/bin/env python
"""Score the p6 schema-gate sample.

Paired design: every decision was also decided by p5/Opus in full-v3 on the same
(case, party), so p5 is a like-for-like baseline. Round 1 (3-valued hallning on
"which side") is kept in out-round1/ for round-over-round comparison.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from aidag.promptgen import HALLNING_TO_ROST  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "data" / "interim" / "p6sample"
R1_MAP = {"utskottet": "Ja", "motforslaget": "Nej", "ingendera": "Avstår"}


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def load(d: Path, man: dict) -> dict:
    out = {}
    for i in sorted(man):
        p = d / f"{i}.json"
        if p.exists():
            try:
                out[i] = json.loads(p.read_text())
            except Exception:  # noqa: BLE001
                pass
    return out


def main() -> None:
    man = {m["id"]: m for m in json.loads((S / "manifest.json").read_text())}
    got = load(S / "out", man)
    r1 = load(S / "out-round1", man)
    n = len(got)
    print(f"round 2: {len(man)} requested | {n} returned    (round 1 on file: {len(r1)})")
    if not n:
        return

    REQ = {"hallning", "confidence", "coverage", "motivering", "citations", "omvarld",
           "flags", "plan_tacker_utskottets_skal"}
    bad = [(i, sorted(set(r) ^ REQ)) for i, r in got.items() if set(r) != REQ]
    badh = [(i, r.get("hallning")) for i, r in got.items() if r.get("hallning") not in HALLNING_TO_ROST]
    print(f"\n=== schema conformance ===")
    print(f"  wrong key set   : {len(bad)} {bad[:3]}")
    print(f"  invalid hallning: {len(badh)} {badh[:3]}")

    # ---- FIX 1: did dropping the third value change anything? ---------------
    print(f"\n=== FIX 1  two-valued hallning ===")
    c = collections.Counter(r["hallning"] for r in got.values())
    print(f"  stodjer {c['stodjer']}/{n} ({c['stodjer']/n:.0%})   avvisar {c['avvisar']}/{n} ({c['avvisar']/n:.0%})")
    if r1:
        c1 = collections.Counter(r1[i]["hallning"] for i in r1)
        print(f"  round 1 was: {dict(c1)}   (ingendera fired {c1.get('ingendera',0)}/{len(r1)})")
    lean = c["stodjer"] / n
    real_nej = sum(1 for i in got if man[i]["actual"] == "Nej") / n
    print(f"  leans to the demand {lean:.0%}  vs real Nej rate {real_nej:.0%}  (round 1 leaned 62%)")

    # ---- FIX 2: is the procedural asymmetry now measured? ------------------
    print(f"\n=== FIX 2  plan_tacker_utskottets_skal (the asymmetry, made explicit) ===")
    pt = collections.Counter(r.get("plan_tacker_utskottets_skal") for r in got.values())
    print(f"  plan speaks to the committee's reason: ja {pt['ja']}/{n} ({pt['ja']/n:.0%})   nej {pt['nej']}/{n} ({pt['nej']/n:.0%})")
    cross = collections.Counter((r["hallning"], r.get("plan_tacker_utskottets_skal")) for r in got.values())
    print(f"  {'':10}{'tacker=ja':>12}{'tacker=nej':>12}")
    for h in ("stodjer", "avvisar"):
        print(f"  {h:10}{cross[(h,'ja')]:>12}{cross[(h,'nej')]:>12}")
    # the diagnostic claim: when the plan is silent on the committee's reason,
    # the stance should lean to the demand far more than when it is not
    for v in ("ja", "nej"):
        sub = [r for r in got.values() if r.get("plan_tacker_utskottets_skal") == v]
        if sub:
            s = sum(1 for r in sub if r["hallning"] == "stodjer") / len(sub)
            agree = sum(1 for i, r in got.items()
                        if r.get("plan_tacker_utskottets_skal") == v
                        and HALLNING_TO_ROST[r["hallning"]] == man[i]["actual"]) / len(sub)
            print(f"  tacker={v}: stodjer {s:.0%}  | derived vote agrees with reality {agree:.0%}  (n={len(sub)})")

    # ---- FIX 3: did uncertainty come back? ---------------------------------
    print(f"\n=== FIX 3  calibration (is 'low'/'not_covered' usable again?) ===")
    for label, get, k5 in (("coverage", lambda r: r["coverage"], "v3_coverage"),
                           ("confidence", lambda r: r["confidence"], "v3_confidence")):
        p6 = collections.Counter(get(r) for r in got.values())
        p5 = collections.Counter(man[i][k5] for i in got)
        old = collections.Counter(get(r1[i]) for i in r1) if r1 else {}
        keys = sorted(set(p6) | set(p5) | set(old))
        print(f"  {label}:")
        for k in keys:
            o = f"{old.get(k,0)/len(r1):6.1%}" if r1 else "    - "
            print(f"    {str(k):12} p5 {p5[k]/n:6.1%}  r1 {o}  ->  r2 {p6[k]/n:6.1%}")

    # ---- citation fidelity -------------------------------------------------
    print(f"\n=== citation fidelity ===")
    tot = ok = 0
    lens = []
    misses = []
    for i, r in got.items():
        req = json.loads((S / "req" / f"{i}.json").read_text())
        src = norm(Path(req["system_file"]).read_text())
        for cit in r.get("citations") or []:
            tot += 1
            q = norm(cit.get("quote", ""))
            lens.append(len(cit.get("quote", "")))
            if q and q in src:
                ok += 1
            else:
                misses.append((i, cit.get("document"), (cit.get("quote") or "")[:60]))
    print(f"  citations {tot} (mean {tot/n:.2f}/decision) | verbatim {ok}/{tot}" + (f" ({ok/tot:.1%})" if tot else ""))
    if lens:
        lens.sort()
        print(f"  median quote {lens[len(lens)//2]} chars")
    for m in misses[:4]:
        print(f"    NOT FOUND {m}")

    # ---- derived vote ------------------------------------------------------
    print(f"\n=== derived vote vs reality (sample oversamples Avstår) ===")
    nonab = [i for i in got if man[i]["actual"] != "Avstår"]
    a6 = sum(1 for i in nonab if HALLNING_TO_ROST[got[i]["hallning"]] == man[i]["actual"])
    a5 = sum(1 for i in nonab if man[i]["v3_rost"] == man[i]["actual"])
    a1 = (sum(1 for i in nonab if i in r1 and R1_MAP[r1[i]["hallning"]] == man[i]["actual"]) if r1 else 0)
    print(f"  excluding abstentions (n={len(nonab)}): p5 {a5/len(nonab):.0%}  |  r1 {a1/len(nonab):.0%}  |  r2 {a6/len(nonab):.0%}")
    GOV = {"M", "KD", "L", "SD"}
    for grp, lbl in ((True, "governing"), (False, "opposition")):
        ids = [i for i in nonab if (man[i]["parti"] in GOV) == grp]
        if ids:
            x = sum(1 for i in ids if HALLNING_TO_ROST[got[i]["hallning"]] == man[i]["actual"])
            y = sum(1 for i in ids if man[i]["v3_rost"] == man[i]["actual"])
            print(f"    {lbl:11} n={len(ids):3}  p5 {y/len(ids):.0%}  ->  r2 {x/len(ids):.0%}")


if __name__ == "__main__":
    main()
