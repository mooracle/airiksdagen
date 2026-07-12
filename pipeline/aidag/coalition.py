"""Coalition discipline vs. party programme — which one explains a vote?

Every division pits the committee's proposal ("Ja") against a reservation
("Nej"). Two rival predictors of how a party votes:

  coalition baseline   always vote Ja, i.e. with the committee. Knows nothing
                       about the party — only that it is on the winning side.
  programme predictor  the simulated agent, which reads that party's own
                       manifesto (and Tidöavtalet where it applies) and never
                       sees who authored the reservation.

The gap between them is the measurement. Where the coalition baseline already
explains the vote and the programme adds nothing, coalition position is doing
the work; where the programme predicts what the baseline cannot, the party is
voting its own documents.

A third, purely procedural fact is used as context but never as a predictor:
whether the party AUTHORED the reservation (alternatives[].source_partier).
Authorship is near-deterministic — parties vote Nej on their own reservations
— which is precisely why it is scrubbed from the agent's context.

  override  the party's own documents explicitly imply opposing the committee,
            yet it voted Ja anyway. Reported at two thresholds: strict
            (explicit coverage AND high confidence) and loose (explicit
            coverage, any confidence), each split by whether the party authored
            the reservation. CAVEAT: an override is a document-grounded reading
            that the party contradicted — it cannot by itself distinguish
            "coalition discipline overrode the programme" from "the agent
            misread the programme". See the methodology page.
"""

from __future__ import annotations

import json
from collections import defaultdict

import polars as pl

from aidag.config import PARTIES, PARTY_CODES, PROCESSED_DIR

# The agent diverges from the committee when it votes anything but Ja.
DIVERGENT = ("Nej", "Avstår")


def reservation_authors() -> dict[str, set[str]]:
    """votering_id -> parties that authored a reservation in that division."""
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").select("votering_id", "alternatives")
    authors: dict[str, set[str]] = {}
    for row in cases.iter_rows(named=True):
        raw = row["alternatives"]
        parties: set[str] = set()
        if raw:
            for alt in json.loads(raw):
                if alt.get("alt_id") != "utskottet":
                    parties.update(alt.get("source_partier") or [])
        authors[row["votering_id"]] = parties
    return authors


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _bucket() -> dict:
    return {"den": 0, "num": 0, "rate": None}


def compute(df: pl.DataFrame) -> dict:
    """df: decisions joined to actual positions, Frånvarande already dropped.

    Requires columns: parti, votering_id, rost, position, coverage, confidence,
    month, datum, rubrik, utskott, motivering, citations.
    """
    authors = reservation_authors()

    per_party: dict[str, dict] = {}
    per_month: list[dict] = []
    override_cases: list[dict] = []

    for p in PARTY_CODES:
        sub = df.filter(pl.col("parti") == p)
        if len(sub) == 0:
            continue

        n = len(sub)
        # Rival predictors, scored against the same actual positions.
        coalition_hits = 0
        programme_hits = 0
        authored_n = 0
        # override[threshold][authorship] -> {den, num, rate}
        override = {
            t: {"all": _bucket(), "authored": _bucket(), "not_authored": _bucket()}
            for t in ("strict", "loose")
        }
        monthly: dict[str, dict] = defaultdict(lambda: {"n": 0, "coalition": 0, "programme": 0})

        for row in sub.iter_rows(named=True):
            actual = row["position"]
            ai = row["rost"]
            authored = p in authors.get(row["votering_id"], set())
            authored_n += authored

            coalition_hits += actual == "Ja"
            programme_hits += ai == actual

            m = monthly[row["month"]]
            m["n"] += 1
            m["coalition"] += actual == "Ja"
            m["programme"] += ai == actual

            # Own documents explicitly imply opposing the committee.
            if ai in DIVERGENT and row["coverage"] == "explicit":
                thresholds = ["loose"] + (["strict"] if row["confidence"] == "high" else [])
                overridden = actual == "Ja"
                for t in thresholds:
                    key = "authored" if authored else "not_authored"
                    for bucket in (override[t]["all"], override[t][key]):
                        bucket["den"] += 1
                        bucket["num"] += overridden
                if overridden:
                    override_cases.append({
                        "votering_id": row["votering_id"],
                        "datum": row["datum"],
                        "rubrik": row["rubrik"],
                        "utskott": row["utskott"],
                        "parti": p,
                        "ai_rost": ai,
                        "actual": actual,
                        "confidence": row["confidence"],
                        "coverage": row["coverage"],
                        "strict": row["confidence"] == "high",
                        "authored_reservation": authored,
                        "motivering": row["motivering"],
                        "citations": row["citations"],
                    })

        for t in ("strict", "loose"):
            for b in override[t].values():
                b["rate"] = _rate(b["num"], b["den"])

        coalition = coalition_hits / n
        programme = programme_hits / n
        per_party[p] = {
            "n": n,
            "bloc": PARTIES[p]["bloc"],
            "coalition_baseline": round(coalition, 4),
            "programme_accuracy": round(programme, 4),
            "programme_lift": round(programme - coalition, 4),
            "authored_reservation_n": authored_n,
            "override": override,
        }

        for month, m in sorted(monthly.items()):
            per_month.append({
                "parti": p,
                "month": month,
                "n": m["n"],
                "coalition_baseline": round(m["coalition"] / m["n"], 4),
                "programme_accuracy": round(m["programme"] / m["n"], 4),
                "programme_lift": round((m["programme"] - m["coalition"]) / m["n"], 4),
            })

    override_cases.sort(key=lambda c: (not c["strict"], c["datum"], c["parti"]))
    return {
        "n_decisions": len(df),
        "per_party": per_party,
        "per_month": per_month,
        "override_cases": override_cases,
    }


def overrides_by_case(result: dict) -> dict[str, dict[str, str]]:
    """votering_id -> {parti: 'strict'|'loose'} for per-case badges."""
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for c in result["override_cases"]:
        out[c["votering_id"]][c["parti"]] = "strict" if c["strict"] else "loose"
    return dict(out)
