"""Guards for the coalition-vs-programme metric.

The metric is published, so its definitions are pinned here: what counts as an
override, at which threshold, and how authorship splits the denominator.
"""

import polars as pl
import pytest

from aidag import coalition
from aidag.export_site import annotate_coalition

VID = "11111111-1111-1111-1111-111111111111"


def _df(rows: list[dict]) -> pl.DataFrame:
    base = {
        "votering_id": VID,
        "parti": "V",
        "rost": "Ja",
        "position": "Ja",
        "coverage": "explicit",
        "confidence": "high",
        "month": "2023-04",
        "datum": "2023-04-12",
        "rubrik": "Testärende",
        "utskott": "FiU",
        "motivering": "m",
        "citations": [],
    }
    return pl.DataFrame([{**base, **r} for r in rows])


@pytest.fixture
def no_authors(monkeypatch):
    monkeypatch.setattr(coalition, "reservation_authors", lambda: {})


def test_baseline_is_always_ja_and_lift_is_the_difference(no_authors):
    # 3 of 4 actual votes are Ja -> coalition baseline 75%.
    # The agent gets 2 of 4 right -> programme 50%, lift -25pp.
    out = coalition.compute(_df([
        {"rost": "Ja", "position": "Ja"},    # both right
        {"rost": "Nej", "position": "Ja"},   # baseline right, programme wrong
        {"rost": "Nej", "position": "Ja"},   # baseline right, programme wrong
        {"rost": "Nej", "position": "Nej"},  # baseline wrong, programme right
    ]))
    v = out["per_party"]["V"]
    assert v["coalition_baseline"] == 0.75
    assert v["programme_accuracy"] == 0.5
    assert v["programme_lift"] == -0.25


def test_override_requires_explicit_divergence_that_voted_ja(no_authors):
    out = coalition.compute(_df([
        # counts: documents explicitly say oppose, party voted Ja anyway
        {"rost": "Nej", "position": "Ja", "coverage": "explicit", "confidence": "high"},
        # not counted: the party actually did oppose -> no override
        {"rost": "Nej", "position": "Nej", "coverage": "explicit", "confidence": "high"},
        # not counted: agent had no explicit basis in the documents
        {"rost": "Nej", "position": "Ja", "coverage": "inferred", "confidence": "high"},
        # not counted: agent agreed with the committee, nothing to override
        {"rost": "Ja", "position": "Ja", "coverage": "explicit", "confidence": "high"},
    ]))
    strict = out["per_party"]["V"]["override"]["strict"]["all"]
    assert (strict["num"], strict["den"]) == (1, 2)  # 2 explicit divergences, 1 overridden
    assert len(out["override_cases"]) == 1


def test_low_confidence_counts_loose_but_not_strict(no_authors):
    out = coalition.compute(_df([
        {"rost": "Nej", "position": "Ja", "coverage": "explicit", "confidence": "medium"},
    ]))
    o = out["per_party"]["V"]["override"]
    assert (o["strict"]["all"]["den"], o["strict"]["all"]["num"]) == (0, 0)
    assert (o["loose"]["all"]["den"], o["loose"]["all"]["num"]) == (1, 1)
    assert out["override_cases"][0]["strict"] is False


def test_authorship_splits_the_denominator(monkeypatch):
    monkeypatch.setattr(coalition, "reservation_authors", lambda: {VID: {"V"}})
    out = coalition.compute(_df([
        {"rost": "Nej", "position": "Ja"},
    ]))
    v = out["per_party"]["V"]
    assert v["authored_reservation_n"] == 1
    assert v["override"]["strict"]["authored"]["den"] == 1
    assert v["override"]["strict"]["not_authored"]["den"] == 0


def test_reservation_authors_parses_source_partier():
    alternatives = [
        {"alt_id": "utskottet", "text": "…", "source_partier": []},
        {"alt_id": "res-3", "text": "Reservation 3", "source_partier": ["V", "MP"]},
    ]
    actual = {"V": {"position": "Nej"}, "M": {"position": "Ja"}}
    ai = {
        # V moved the reservation and voted for it -> no override, but flagged as author
        "V": {"rost": "Nej", "coverage": "explicit", "confidence": "high"},
        # M's documents explicitly implied opposing, yet M voted Ja -> override
        "M": {"rost": "Nej", "coverage": "explicit", "confidence": "high"},
    }
    annotate_coalition(alternatives, actual, ai)

    assert actual["V"]["authored_reservation"] is True
    assert actual["M"]["authored_reservation"] is False
    assert ai["V"]["program_override"] is None
    assert ai["M"]["program_override"] == "strict"


def test_badge_matches_the_aggregate_definition(no_authors):
    """The per-case badge and the roll-up must not drift apart."""
    rows = [
        {"rost": "Nej", "position": "Ja", "coverage": "explicit", "confidence": "high"},
        {"rost": "Nej", "position": "Ja", "coverage": "explicit", "confidence": "low"},
        {"rost": "Ja", "position": "Ja", "coverage": "explicit", "confidence": "high"},
    ]
    out = coalition.compute(_df(rows))
    from_aggregate = sorted(
        "strict" if c["strict"] else "loose" for c in out["override_cases"]
    )

    badges = []
    for r in rows:
        actual = {"V": {"position": r["position"]}}
        ai = {"V": {k: r[k] for k in ("rost", "coverage", "confidence")}}
        annotate_coalition([{"alt_id": "utskottet", "source_partier": []}], actual, ai)
        if ai["V"]["program_override"]:
            badges.append(ai["V"]["program_override"])

    assert sorted(badges) == from_aggregate == ["loose", "strict"]
