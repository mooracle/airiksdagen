"""Shared Pydantic schemas. site/src/types.ts mirrors these for the Astro build."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Rost = Literal["Ja", "Nej", "Avstår", "Frånvarande"]
SimRost = Literal["Ja", "Nej", "Avstår"]


class Alternative(BaseModel):
    """A substantive alternative in the vote (committee proposal or reservation).

    Party authorship is kept here for the site but stripped from prompts
    (anonymized as 'Alternativ A/B') to avoid near-ground-truth leakage.
    """

    alt_id: str  # "utskottet" or "res-<nummer>"
    text: str
    source_partier: list[str] = []


class Case(BaseModel):
    """One votering (chamber vote on a förslagspunkt) — the simulation unit."""

    votering_id: str
    rm: str
    beteckning: str
    punkt: int
    dok_id: str
    datum: str  # YYYY-MM-DD (beslutsdag)
    utskott: str  # organ code, e.g. "AU"
    rubrik: str
    forslag_text: str
    notis: str = ""
    vinnare: str = ""
    alternatives: list[Alternative] = []
    kb_month: str  # YYYY-MM


class PartyPosition(BaseModel):
    """Actual party behaviour on one votering, aggregated from per-MP votes."""

    votering_id: str
    parti: str
    n_ja: int
    n_nej: int
    n_avstar: int
    n_franvarande: int
    position: Rost  # majority of cast votes; Frånvarande if nobody voted
    cohesion: float  # share of cast votes matching position; 1.0 if none cast
    seats: int


class Citation(BaseModel):
    document: Literal["valmanifest", "partiprogram", "tidoavtalet"]
    quote: str


class Decision(BaseModel):
    """One AI party-agent decision on one case (one JSONL line in results)."""

    votering_id: str
    parti: str
    run_id: str
    prompt_version: str
    model: str
    arm: str = "anonymous"  # "anonymous" (default) or "labeled" pilot arm
    rost: SimRost
    confidence: Literal["high", "medium", "low"]
    coverage: Literal["explicit", "inferred", "not_covered"]
    motivering: str
    citations: list[Citation] = []
    flags: list[str] = []
    usage: dict[str, int] = {}
    batch_id: str = ""
    collected_at: str = ""


class Probe(BaseModel):
    """Memorization probe: the model's cold recall of the real outcome."""

    votering_id: str
    run_id: str
    model: str
    predicted_positions: dict[str, str]  # parti -> rost, as recalled by the model
    actual_positions: dict[str, str] = {}
    exact_match_count: int = 0
    recalls_case: bool = False
    raw_answer: str = ""
    batch_id: str = ""


class KBIndicator(BaseModel):
    """One macro indicator value together with the vintage it was known at."""

    series: str
    label: str
    value: float
    unit: str
    period: str  # what period the value describes, e.g. "2023-10"
    vintage_date: str  # when this value was publicly known (must be <= snapshot end)
    source_url: str


class KBEvent(BaseModel):
    date: str
    text: str
    source_url: str


class KBSnapshot(BaseModel):
    """Point-in-time 'state of the country' for one month.

    The no-future-information rule: every indicator's vintage_date and every
    event date must fall on or before the last day of `month`.
    """

    month: str  # YYYY-MM
    government: dict
    indicators: list[KBIndicator]
    events: list[KBEvent]
