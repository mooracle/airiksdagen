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
    # every document any prompt version can serve (see corpus.DOCS_P4/DOCS_P5).
    # Whether a given decision's agent was ACTUALLY served the document it cites
    # is enforced separately, per party and per date, by the verify gate.
    document: Literal["valmanifest", "partiprogram", "tidoavtalet", "budgetmotion"]
    quote: str
    # Short label (2-6 words) of the commitment the quote expresses, e.g.
    # "minskad asylinvandring". Empty in pre-p3 runs. The FIRST citation in a
    # decision is the decisive one — the passage that stands behind the vote.
    princip: str = ""


class OmvarldFaktor(BaseModel):
    faktor: str  # e.g. "hög inflation", "kriget i Ukraina"
    effekt: str  # how it modulates the document-based line


class Omvarld(BaseModel):
    """Worldstate reference: did events/economy materially affect the vote?

    The party documents remain the normative basis; this records when the
    agent judged that the situation changed how they apply (p4+)."""

    paverkar: bool = False
    faktorer: list[OmvarldFaktor] = []


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
    omvarld: Omvarld = Omvarld()  # p4+; default for older runs
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


class PartySupport(BaseModel):
    """Opinion-poll standing: monthly average across published polls."""

    parties: dict[str, float]  # parti -> support %
    n_polls: int
    period: str  # YYYY-MM the polls were published in
    vintage_date: str  # latest publication date used (must be <= snapshot end)
    source_url: str


class KBSnapshot(BaseModel):
    """Point-in-time 'state of the country' for one month.

    The no-future-information rule: every indicator's vintage_date, the poll
    vintage, and every event date must fall on or before the last day of
    `month`.
    """

    month: str  # YYYY-MM
    government: dict
    indicators: list[KBIndicator]
    party_support: PartySupport | None = None
    events: list[KBEvent]
