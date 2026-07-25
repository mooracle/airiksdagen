#!/usr/bin/env python
"""Score a p6 validation run — the accuracy test party M could not provide.

  uv run python scripts/p6val_report.py V2024

Reports, on a mixed-outcome sample:
  * derived-vote accuracy vs the real vote (Ja/Nej cases only — the two-valued
    hallning cannot express an abstention), against the majority-class baseline
  * how the model behaves on the real abstentions (does it flag uncertainty?)
  * citation fidelity (must stay 100% verbatim, as on party M)
  * the confusion matrix, so a one-sided failure (e.g. always predicts Ja) shows
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


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def main() -> None:
    # ARGS: [context] [out-dir] — see p6val_gap_report for the manifest pairing.
    name = sys.argv[1] if len(sys.argv) > 1 else "V2024"
    sub = sys.argv[2] if len(sys.argv) > 2 else "out"
    d = ROOT / "data" / "interim" / "p6val" / name
    # "out60" -> manifest60.json, but only if it exists: "out2" is a second pass
    # over the ORIGINAL set, not a 2-case set, and must keep manifest.json.
    mf = (f"manifest{sub[3:]}.json"
          if sub[3:].isdigit() and (d / f"manifest{sub[3:]}.json").exists()
          else "manifest.json")
    man = {m["cid"]: m for m in json.loads((d / mf).read_text())}
    src = norm((d / "system" / f"{man['000']['parti']}.txt").read_text())

    got = {}
    for line in (d / sub / "decisions.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            got[str(r["cid"])] = r
    print(f"{name}: {len(man)} requested | {len(got)} returned "
          f"(party {man['000']['parti']})\n")
    if not got:
        return

    # ---- accuracy on Ja/Nej -------------------------------------------------
    jn = [i for i in got if man[i]["actual"] in ("Ja", "Nej")]
    correct = sum(1 for i in jn if HALLNING_TO_ROST.get(got[i]["hallning"]) == man[i]["actual"])
    maj = collections.Counter(man[i]["actual"] for i in jn).most_common(1)[0]
    print("=== derived vote vs reality (Ja/Nej cases) ===")
    print(f"  accuracy      {correct}/{len(jn)} ({correct/len(jn):.0%})")
    print(f"  majority base {maj[1]}/{len(jn)} ({maj[1]/len(jn):.0%})  (always-{maj[0]})")
    # confusion: real (rows) x derived (cols)
    conf = collections.Counter((man[i]["actual"], HALLNING_TO_ROST.get(got[i]["hallning"], "?"))
                               for i in jn)
    print(f"  {'':10}{'->Ja':>8}{'->Nej':>8}")
    for real in ("Ja", "Nej"):
        print(f"  real {real:<5}{conf[(real,'Ja')]:>8}{conf[(real,'Nej')]:>8}")

    # ---- behaviour on real abstentions --------------------------------------
    ab = [i for i in got if man[i]["actual"] == "Avstår"]
    if ab:
        unc = sum(1 for i in ab if got[i]["coverage"] == "not_covered"
                  or got[i]["confidence"] == "low")
        lean = collections.Counter(got[i]["hallning"] for i in ab)
        print(f"\n=== real abstentions (n={len(ab)}) — hallning can't express Avstår ===")
        print(f"  flagged uncertain (not_covered or low conf): {unc}/{len(ab)}")
        print(f"  forced lean: {dict(lean)}")

    # ---- governing-vs-opposition split is moot here (single party) ----------
    print(f"\n=== confidence / coverage calibration ===")
    for i in ("confidence", "coverage"):
        c = collections.Counter(got[k][i] for k in got)
        # is the model MORE right when it says high confidence?
        for level in c:
            ids = [k for k in jn if got[k][i] == level]
            if ids:
                acc = sum(1 for k in ids if HALLNING_TO_ROST.get(got[k]["hallning"]) == man[k]["actual"]) / len(ids)
                print(f"  {i}={level:<12} n={len(ids):>3}  Ja/Nej accuracy {acc:.0%}")

    # ---- citation fidelity --------------------------------------------------
    tot = ok = 0
    lens = []
    miss = []
    for i, r in got.items():
        for c in r.get("citations") or []:
            tot += 1
            q = c.get("quote", "")
            lens.append(len(q))
            if q and norm(q) in src:
                ok += 1
            else:
                miss.append((i, (q or "")[:70]))
    lens.sort()
    print(f"\n=== citation fidelity ===")
    print(f"  {tot} citations ({tot/len(got):.2f}/dec) | verbatim {ok}/{tot} "
          f"({ok/tot:.0%}) | median {lens[len(lens)//2]}c")
    for m in miss[:5]:
        print(f"    NOT VERBATIM [{m[0]}] {m[1]}")


if __name__ == "__main__":
    main()
