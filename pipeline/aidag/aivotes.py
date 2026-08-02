"""AI-vote analytics — the chamber as the parties' own documents describe it.

Companion to `analytics.py`, which answers the same questions from the real
votes with no AI involved. Everything here reads the plan-implied vote
(`ai[parti].rost`, derived from the p6 stance) instead of the floor position, so
the two modules produce directly comparable numbers. Called by `export_site`,
which already holds decisions, positions and case metadata in memory.

  pairs_matrix()   8x8 party-pair agreement over the plan-implied votes
  flip()           would the chamber have decided otherwise on documented
                   commitments alone?
  area_stats()     per policy area x party: how often the vote followed the plan

The `explicit` tier gate is load-bearing in `flip()` and reported separately in
`area_stats()`. It is the same gate the published gap headline uses: a plan that
never reached what the vote turned on cannot show a party abandoning it, so only
`explicit` decisions are allowed to move a vote here. See gap.py.
"""

from __future__ import annotations

from aidag.config import PARTY_CODES

# A counterfactual is only defined where the party actually took a side. A real
# abstention is a floor tactic the plan was never asked to express, so an
# abstaining party keeps its abstention rather than being moved onto an axis it
# deliberately stayed off. Matches the `missx` rule in export_site.
DECIDED = ("Ja", "Nej")


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _outcome(ja: int, nej: int) -> str | None:
    """Ja / Nej, or None on an exact tie.

    The Riksdag breaks a tie by lot, so a tied counterfactual has no determinate
    result and must not be reported as a flip in either direction.
    """
    if ja == nej:
        return None
    return "Ja" if ja > nej else "Nej"


def flip(actual: dict[str, dict], ai: dict[str, dict]) -> dict | None:
    """Re-run one division with documented commitments overriding the floor.

    A party is moved only when its own plan stated the commitment outright
    (evidence tier `explicit`), it voted Ja or Nej, and the plan pointed the
    other way — the same set `export_site` publishes as `missx`. Every other
    party keeps its real seat counts, including its dissenters and abstainers.

    A moved party's whole cast contingent (Ja + Nej + Avstår) goes to the
    plan-implied side: the counterfactual being modelled is "this party's line
    was the other one", and a line change carries the members who turned up.
    Leaving that party's abstainers abstaining instead changes no outcome in the
    full-v4 corpus, so nothing in the published count turns on the choice.

    Returns None when no party qualifies — there is no counterfactual to state.
    """
    movers = [
        p
        for p, d in ai.items()
        if d.get("tier") == "explicit"
        and actual.get(p, {}).get("position") in DECIDED
        and actual[p]["position"] != d.get("rost")
    ]
    if not movers:
        return None

    real_ja = sum(a["n_ja"] for a in actual.values())
    real_nej = sum(a["n_nej"] for a in actual.values())
    cf_ja = cf_nej = 0
    for p, a in actual.items():
        if p in movers:
            cast = a["n_ja"] + a["n_nej"] + a["n_avstar"]
            rost = ai[p]["rost"]
            if rost == "Ja":
                cf_ja += cast
            elif rost == "Nej":
                cf_nej += cast
        else:
            cf_ja += a["n_ja"]
            cf_nej += a["n_nej"]

    real = _outcome(real_ja, real_nej)
    counterfactual = _outcome(cf_ja, cf_nej)
    return {
        # hemicycle order is applied by the caller (export_site sorts `missx`);
        # sorted here only so the payload is stable across runs
        "parties": sorted(movers),
        "actual": {"ja": real_ja, "nej": real_nej, "outcome": real},
        "counterfactual": {"ja": cf_ja, "nej": cf_nej, "outcome": counterfactual},
        # a tie on either side is indeterminate, never a flip
        "flips": bool(real and counterfactual and real != counterfactual),
    }


def pairs_matrix(cases: list[dict]) -> dict:
    """% of divisions where two parties' plan-implied votes matched.

    `cases` is one dict per division: {"rm": str, "ai": {parti: rost}}. Mirrors
    `analytics.pairs_matrix` cell for cell, so the two tables can be read side by
    side and subtracted. The denominator is divisions where both parties have a
    decision — unlike the real matrix there is no absence to exclude, because
    every party has a plan for every division.
    """

    def matrix_for(rows: list[dict]) -> dict:
        agree = {a: {b: 0 for b in PARTY_CODES} for a in PARTY_CODES}
        total = {a: {b: 0 for b in PARTY_CODES} for a in PARTY_CODES}
        for row in rows:
            votes = row["ai"]
            parties = [p for p in PARTY_CODES if p in votes]
            for i, a in enumerate(parties):
                for b in parties[i:]:
                    total[a][b] += 1
                    total[b][a] += 1
                    if votes[a] == votes[b]:
                        agree[a][b] += 1
                        agree[b][a] += 1
        return {
            a: {
                b: round(agree[a][b] / total[a][b], 3) if total[a][b] else None
                for b in PARTY_CODES
            }
            for a in PARTY_CODES
        }

    out = {"overall": matrix_for(cases), "per_rm": {}}
    for rm in sorted({c["rm"] for c in cases}):
        out["per_rm"][rm] = matrix_for([c for c in cases if c["rm"] == rm])
    return out


def flip_summary(cases: list[dict]) -> dict:
    """Corpus-level view of the counterfactual, plus every division it changed.

    `cases` is one dict per division carrying the display fields and the
    `flip` block `flip()` already produced, so nothing is recomputed here.

    `cases` is the full list, uncapped — the site slices it for display and can
    state its own cap. Every count in the summary is therefore a real total.

    `solo` marks a division that one party's documented commitments would have
    turned on their own. It is the strongest single claim available: no coalition
    of broken promises needed, one party voting its own manifesto was enough.
    """
    flipped = []
    n_movers = n_indeterminate = 0
    direction = {"ja_to_nej": 0, "nej_to_ja": 0}
    per_party: dict[str, dict] = {p: {"movers": 0, "flips": 0, "solo": 0} for p in PARTY_CODES}
    per_area: dict[str, dict] = {}

    for case in cases:
        f = case.get("flip")
        area = case.get("policy_area")
        if area:
            bucket = per_area.setdefault(area, {"n_cases": 0, "with_movers": 0, "flipped": 0})
            bucket["n_cases"] += 1
        if not f:
            continue
        n_movers += 1
        if area:
            per_area[area]["with_movers"] += 1
        for p in f["parties"]:
            per_party[p]["movers"] += 1
        if f["actual"]["outcome"] is None or f["counterfactual"]["outcome"] is None:
            n_indeterminate += 1
        if not f["flips"]:
            continue
        solo = len(f["parties"]) == 1
        for p in f["parties"]:
            per_party[p]["flips"] += 1
            if solo:
                per_party[p]["solo"] += 1
        if area:
            per_area[area]["flipped"] += 1
        direction["ja_to_nej" if f["actual"]["outcome"] == "Ja" else "nej_to_ja"] += 1
        flipped.append({
            "votering_id": case["votering_id"],
            "datum": case["datum"],
            "rubrik": case["rubrik"],
            "utskott": case["utskott"],
            "policy_area": area,
            "parties": f["parties"],
            "solo": solo,
            "outcome": f["actual"]["outcome"],
            "counterfactual": f["counterfactual"]["outcome"],
            # real margin, so the reader can see whether the division was close
            # before the counterfactual touched it
            "margin": abs(f["actual"]["ja"] - f["actual"]["nej"]),
        })

    flipped.sort(key=lambda c: (c["datum"], c["votering_id"]), reverse=True)
    return {
        "n_cases": len(cases),
        "n_with_movers": n_movers,
        "n_flipped": len(flipped),
        "n_solo": sum(1 for c in flipped if c["solo"]),
        # a tie on either side of the comparison: the Riksdag would have drawn
        # lots, so these are excluded from n_flipped in both directions
        "n_indeterminate": n_indeterminate,
        "direction": direction,
        "per_party": {p: v for p, v in per_party.items() if v["movers"]},
        "per_area": {
            a: {**v, "flip_rate": _rate(v["flipped"], v["n_cases"])}
            for a, v in sorted(per_area.items(), key=lambda kv: -kv[1]["flipped"])
        },
        "cases": flipped,
    }


def _block() -> dict:
    return {"n": 0, "follows": 0, "abstained": 0, "explicit_n": 0, "explicit_gap": 0}


def _finish(b: dict) -> dict:
    return {
        "n": b["n"],
        "follows": b["follows"],
        "follow_rate": _rate(b["follows"], b["n"]),
        # outside `n`, not a miss inside it — see area_stats
        "abstained": b["abstained"],
        "explicit_n": b["explicit_n"],
        "explicit_gap": b["explicit_gap"],
        "explicit_gap_rate": _rate(b["explicit_gap"], b["explicit_n"]),
    }


def area_stats(cases: list[dict]) -> dict:
    """Per policy area x party: how often the real vote followed the plan.

    `cases` is one dict per division: {"policy_area": str|None, "actual": ...,
    "ai": ...}. Divisions with no policy area (metadata not generated) are
    skipped rather than pooled into a bucket that would not be a policy area.

    Two numbers per cell, and they answer different questions:

      follow_rate       all evidence tiers — how often the party's vote matched
                        what its plan points at. The descriptive number, and the
                        only one with enough n to break down 8 parties x 15
                        areas.
      explicit_gap_rate the `explicit` tier only — how often a plan that stated
                        the commitment outright was voted against. The stronger
                        claim, on a much thinner denominator; some cells are
                        single digits, so it is reported with its n attached and
                        never used to rank areas on its own.

    Both denominators are votes where the party took a side (Ja/Nej), the same
    gate gap.py uses. Counting abstentions as misses would be a measurement
    artefact rather than a finding: a p6 stance derives only to Ja or Nej, so an
    abstention can never be "followed" and any area that attracts more of them
    would read as a party following its plan less there for purely structural
    reasons. Abstentions are counted beside `n` instead, since how often a party
    stays off the axis is itself an area signal.

    A party's `per_party` block is computed from the same rows as its area cells,
    so "follows more in area X than usual" is a within-party comparison and not
    an artefact of areas having different case mixes.
    """
    per_area: dict[str, dict] = {}
    area_totals: dict[str, dict] = {}
    party_totals: dict[str, dict] = {p: _block() for p in PARTY_CODES}
    case_counts: dict[str, int] = {}
    overall = _block()

    for case in cases:
        area = case.get("policy_area")
        if not area:
            continue
        ai = case.get("ai") or {}
        actual = case.get("actual") or {}
        case_counts[area] = case_counts.get(area, 0) + 1
        cells = per_area.setdefault(area, {})
        area_totals.setdefault(area, _block())
        for p, d in ai.items():
            position = actual.get(p, {}).get("position")
            # an absent party has no vote at all; an abstaining one took no side
            if position in (None, "Frånvarande"):
                continue
            blocks = (cells.setdefault(p, _block()), area_totals[area], party_totals[p], overall)
            if position not in DECIDED:
                for b in blocks:
                    b["abstained"] += 1
                continue
            follows = d.get("rost") == position
            for b in blocks:
                b["n"] += 1
                b["follows"] += follows
                if d.get("tier") == "explicit":
                    b["explicit_n"] += 1
                    b["explicit_gap"] += not follows

    return {
        # areas ordered by case count, so the site renders the best-evidenced
        # columns first without having to re-derive the ordering
        "areas": sorted(case_counts, key=lambda a: (-case_counts[a], a)),
        "per_area": {
            area: {
                "n_cases": case_counts[area],
                "all_parties": _finish(area_totals[area]),
                "per_party": {p: _finish(b) for p, b in sorted(cells.items())},
            }
            for area, cells in per_area.items()
        },
        "per_party": {p: _finish(b) for p, b in party_totals.items() if b["n"]},
        "overall": _finish(overall),
    }
