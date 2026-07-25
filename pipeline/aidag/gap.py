"""Plan-vs-behaviour gap — the p6 product.

p6 decouples what a party's own plan implies (`hallning`, a stance on what the
counter-proposal DEMANDS) from how the party actually voted on the floor. The
product is the **gap** between the two, so vote agreement is not a score here:
a divergence is the signal, not an error. `aggregate` keeps agreement only as a
calibration/contamination number.

Two things make a raw gap rate unpublishable on its own, and both are measured
here rather than left implicit.

**1. The two sides of a division are not the same kind of claim.** The
committee's position is frequently procedural ("an inquiry is ongoing, do not
preempt it") while the reservation is substantive ("do X now"). Party plans
speak to substance and are silent on procedure, so "does the plan back the
demand?" skews toward the demand for *every* party. `promptgen._p6_schema`
measured 62% `stodjer` at the gate against a real 44% Nej rate; production runs
higher still. `stance_skew` reports that skew — including how often all eight
parties land on the same stance, which is the tell that the brief, not the
plan, is driving the answer.

**2. Only some decisions can carry a promise-broken claim.** Every decision is
a real, fully cited actor vote, but a plan that never reached what the vote
turned on cannot convict a party of defying it. `promptgen.evidence_tier`
splits decisions into `explicit` / `extrapolated` / `off_axis` from fields the
model already produced. The **headline gap is the `explicit` tier only**; the
wider tiers are reported beside it, never folded into it.

Bloc documents (Tidöavtalet, budget motions) are deliberately out of the p6
corpus: each party is read against its own manifesto and party programme alone.
A governing party whose vote follows a coalition agreement it was not shown is
therefore *expected* to diverge — for M/KD/L that divergence is the finding,
not a defect.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import polars as pl

from aidag.config import PARTY_CODES
from aidag.promptgen import HALLNING_TO_ROST, evidence_tier

# A gap is only defined where the party actually took a side. A real abstention
# is a floor tactic the plan was never asked to predict, so it is reported
# separately instead of being scored as a miss.
DECIDED = ("Ja", "Nej")

TIERS = ("explicit", "extrapolated", "off_axis")


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _is_gap(row: dict) -> bool:
    """The plan-implied vote differs from how the party actually voted."""
    return HALLNING_TO_ROST[row["hallning"]] != row["position"]


def _gap_block(rows: list[dict]) -> dict:
    """n / gap / rate / direction over an already-filtered set of decisions."""
    gaps = [r for r in rows if _is_gap(r)]
    direction = Counter(
        "plan_nej_voted_ja" if HALLNING_TO_ROST[r["hallning"]] == "Nej" else "plan_ja_voted_nej"
        for r in gaps
    )
    return {
        "n": len(rows),
        "gap": len(gaps),
        "aligned": len(rows) - len(gaps),
        "gap_rate": _rate(len(gaps), len(rows)),
        "direction": {
            "plan_nej_voted_ja": direction["plan_nej_voted_ja"],
            "plan_ja_voted_nej": direction["plan_ja_voted_nej"],
        },
    }


def compute(df: pl.DataFrame) -> dict | None:
    """Gap statistics for a p6 run, or None for a run without `hallning`.

    `df` is aggregate's decisions-joined-to-positions frame: one row per
    (votering, party) with `hallning`, `position`, `coverage`, `confidence`,
    `plan_tacker_utskottets_skal`. Returns None on p4/p5 runs so the caller can
    skip writing gap.json rather than emitting an empty file.
    """
    if "hallning" not in df.columns:
        return None
    rows = [r for r in df.iter_rows(named=True) if r.get("hallning")]
    if not rows:
        return None

    for r in rows:
        r["tier"] = evidence_tier(r)

    decided = [r for r in rows if r["position"] in DECIDED]
    explicit = [r for r in decided if r["tier"] == "explicit"]

    # --- headline: the explicit tier, the only one that can carry the claim ---
    headline = _gap_block(explicit)

    # --- the same measure at every tier, so the reader can see the dilution ---
    by_tier = {t: _gap_block([r for r in decided if r["tier"] == t]) for t in TIERS}
    by_tier["all"] = _gap_block(decided)

    # --- per party, at the explicit tier and across all tiers -------------
    per_party = {}
    for p in PARTY_CODES:
        p_all = [r for r in rows if r["parti"] == p]
        if not p_all:
            continue
        p_dec = [r for r in decided if r["parti"] == p]
        p_exp = [r for r in explicit if r["parti"] == p]
        per_party[p] = {
            "n_decisions": len(p_all),
            "explicit": _gap_block(p_exp),
            "all_tiers": _gap_block(p_dec),
            "tiers": {t: sum(1 for r in p_all if r["tier"] == t) for t in TIERS},
            "stodjer_rate": _rate(sum(1 for r in p_all if r["hallning"] == "stodjer"), len(p_all)),
        }

    # --- internal validity: does the gap concentrate where the model itself
    # flagged a weak read? A coherent detector is monotone (explicit < inferred,
    # high < low, plan reaches the committee's reason < does not). A flat table
    # means the "gap" is not tracking the strength of the plan-reading.
    validity = {}
    for field in ("coverage", "confidence", "plan_tacker_utskottets_skal"):
        levels = defaultdict(list)
        for r in decided:
            levels[str(r.get(field))].append(r)
        validity[field] = {
            lv: {"n": len(rs), "gap_rate": _rate(sum(1 for r in rs if _is_gap(r)), len(rs))}
            for lv, rs in sorted(levels.items())
        }

    # --- stance skew: the caveat, quantified ------------------------------
    by_case = defaultdict(dict)
    for r in rows:
        by_case[r["votering_id"]][r["parti"]] = r["hallning"]
    full = [h for h in by_case.values() if len(h) == len(PARTY_CODES)]
    unanimous_stodjer = sum(1 for h in full if all(v == "stodjer" for v in h.values()))
    unanimous_avvisar = sum(1 for h in full if all(v == "avvisar" for v in h.values()))

    abstentions = [r for r in rows if r["position"] == "Avstår"]
    stance_skew = {
        "stodjer_rate": _rate(sum(1 for r in rows if r["hallning"] == "stodjer"), len(rows)),
        "n_cases_all_parties": len(full),
        "unanimous_stodjer": unanimous_stodjer,
        "unanimous_avvisar": unanimous_avvisar,
        "unanimous_rate": _rate(unanimous_stodjer + unanimous_avvisar, len(full)),
        # real abstentions are excluded from the gap; kept visible so the
        # excluded slice cannot be mistaken for a rounding difference.
        "n_real_abstentions_excluded": len(abstentions),
    }

    return {
        "n_decisions": len(rows),
        "n_decided": len(decided),
        "tiers": {t: sum(1 for r in rows if r["tier"] == t) for t in TIERS},
        "headline": headline,
        "by_tier": by_tier,
        "per_party": per_party,
        "validity": validity,
        "stance_skew": stance_skew,
    }


def report(g: dict) -> str:
    """Operator-facing summary — the numbers a run should be judged on."""
    out = []
    h = g["headline"]
    t = g["tiers"]
    out.append(
        f"  tiers: explicit {t['explicit']} | extrapolated {t['extrapolated']} "
        f"| off_axis {t['off_axis']}"
    )
    out.append(
        f"  HEADLINE gap (explicit tier): {h['gap']}/{h['n']} = "
        f"{h['gap_rate']:.0%}" if h["gap_rate"] is not None else "  HEADLINE gap: n/a"
    )
    out.append(
        f"    direction: plan→Nej voted Ja {h['direction']['plan_nej_voted_ja']} | "
        f"plan→Ja voted Nej {h['direction']['plan_ja_voted_nej']}"
    )
    s = g["stance_skew"]
    out.append(
        f"  stance skew: {s['stodjer_rate']:.0%} stödjer | "
        f"{s['unanimous_stodjer']}/{s['n_cases_all_parties']} cases all 8 parties stödjer"
    )
    out.append(f"  {'':4} {'n_expl':>7} {'gap':>6} {'gap rate':>9} {'stödjer':>9}")
    for p, s2 in g["per_party"].items():
        e = s2["explicit"]
        rate = f"{e['gap_rate']:.0%}" if e["gap_rate"] is not None else "—"
        out.append(
            f"  {p:4} {e['n']:>7} {e['gap']:>6} {rate:>9} {s2['stodjer_rate']:>8.0%}"
        )
    return "\n".join(out)
