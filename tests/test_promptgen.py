"""Golden tests for prompt rendering: leakage guards and Tidö gating.

These are the checks behind `aidag verify prompts` — if any of them fail, the
no-future-information / no-identifier guarantees of the methodology are broken.
"""

import re

import pytest

from aidag.promptgen import (
    DECISION_SCHEMA_P6,
    FORBIDDEN_PATTERNS,
    build_system_blocks,
    coarse_time,
    derive_rost,
    p6_decidable,
    render_user_message,
    scrub_text,
    tido_applies,
)

# Some checks below assert the p5 rendering specifically and must pin the version
# rather than ride the PROMPT_VERSION default, which is now p6. Two reasons: p6
# builds <arende> from the committed casemeta brief instead of the case dict, so
# these fixtures stop being hermetic; and p6 serves the party's own plan only, so
# there is no Tidöavtalet block to gate. The leakage guards that hold for every
# version are deliberately left on the default so they cover the current one.
P5 = "p5"

CASE = {
    "votering_id": "91110125-72B3-4C4F-8B1A-584C5616EF08",
    "rm": "2022/23",
    "beteckning": "AU10",
    "punkt": 1,
    "dok_id": "HA01AU10",
    "datum": "2023-06-07",
    "utskott": "AU",
    "rubrik": "Regeringens lagförslag",
    "dok_titel": "En fortsatt stärkt arbetslöshetsförsäkring",
    "forslag_text": (
        "Riksdagen antar regeringens förslag till lag om ändring i lagen (1997:238) om "
        "arbetslöshetsförsäkring. Därmed bifaller riksdagen proposition 2022/23:85 punkterna 1-6 "
        "och avslår motion 2022/23:2372 av Jonny Cato och Helena Vilhelmsson (båda C)."
    ),
    "notis": "Riksdagen sa ja till regeringens förslag …",
    "utsknotis": "Utskottet föreslår att riksdagen antar regeringens förslag beslutat 2023-06-01.",
    "alternatives": [
        {"alt_id": "utskottet", "text": "Bifall till propositionen", "source_partier": []},
        {"alt_id": "res-1", "text": "Reservation 1", "source_partier": ["C"]},
    ],
    "kb_month": "2023-06",
}


def test_no_identifiers_in_anonymous_prompt():
    msg = render_user_message(CASE, arm="anonymous")
    for pattern in FORBIDDEN_PATTERNS:
        assert not re.search(pattern, msg), f"leaked pattern {pattern}: {re.search(pattern, msg).group()}"


def test_no_outcome_leak():
    msg = render_user_message(CASE, arm="anonymous")
    assert "Riksdagen sa ja" not in msg  # post-decision notis must never appear
    assert "sa ja" not in msg.lower()


def test_author_party_stripped_in_anonymous_arm():
    msg = render_user_message(CASE, arm="anonymous")
    assert "Jonny Cato" not in msg
    assert "(båda C)" not in msg
    assert "Alternativ A:" in msg  # the counter-proposal renders (label or substance)
    assert "(C)" not in msg


# "m.fl." (and initials) contain periods; the author clause must still be
# stripped in the anonymous arm — the pre-fix AUTHOR_RE stopped at the period
# and leaked "(MP)"/"(V)" tags for these very common motions.
MFL_CASE = {
    **CASE,
    "forslag_text": (
        "Riksdagen antar regeringens förslag. Därmed avslår riksdagen motionerna\n\n"
        "2025/26:1 av Annika Hirvonen m.fl. (MP) yrkandena 1-7 och\n\n"
        "2025/26:2 av Tony Haddou m.fl. (V) yrkande 1."
    ),
}


def test_author_clause_with_mfl_stripped():
    msg = render_user_message(MFL_CASE, arm="anonymous")
    assert "(MP)" not in msg and "(V)" not in msg
    assert "Annika Hirvonen" not in msg and "Tony Haddou" not in msg


def test_no_party_tag_survives_anonymous_render():
    from aidag.promptgen import PARTY_TAG_RE

    for case in (CASE, MFL_CASE):
        msg = render_user_message(case, arm="anonymous")
        m = PARTY_TAG_RE.search(msg)
        assert not m, f"party tag {m.group()} leaked into anonymous prompt"


def test_labeled_arm_keeps_party_tag_from_prose():
    # the labeled arm must NOT scrub authorship — it's the measured contrast
    msg = render_user_message(MFL_CASE, arm="labeled", prompt_version=P5)
    assert "(MP)" in msg and "Annika Hirvonen" in msg


def test_labeled_arm_keeps_reservation_party():
    msg = render_user_message(CASE, arm="labeled", prompt_version=P5)
    assert "Alternativ A (C):" in msg


# The reservation-substance layer feeds the party-blind argument of each
# counter-proposal into the prompt, so the agent can weigh a Nej on substance
# rather than an opaque "Reservation N". Attached to the alt here so the test is
# hermetic (no dependency on the results JSONL).
SUBST_CASE = {
    **CASE,
    "alternatives": [
        {"alt_id": "utskottet", "text": "Bifall till propositionen", "source_partier": []},
        {
            "alt_id": "res-1",
            "text": "Reservation 1",
            "source_partier": ["C"],
            "substance": {
                "sv": "Motförslaget vill att arbetslöshetsförsäkringen ska omfatta fler egenföretagare.",
                "en": "The counter-proposal wants unemployment insurance to cover more self-employed people.",
            },
        },
    ],
}


def test_reservation_substance_rendered_anonymous():
    msg = render_user_message(SUBST_CASE, arm="anonymous", prompt_version=P5)
    assert "Alternativ A: Motförslaget vill att arbetslöshetsförsäkringen" in msg
    assert "Reservation 1" not in msg  # opaque label replaced by the substance


def test_reservation_substance_is_leak_free():
    msg = render_user_message(SUBST_CASE, arm="anonymous")
    for pattern in FORBIDDEN_PATTERNS:
        assert not re.search(pattern, msg), f"leaked pattern {pattern} via substance"
    from aidag.promptgen import PARTY_TAG_RE

    i, j = msg.find("<arende>"), msg.find("</arende>")
    assert not PARTY_TAG_RE.search(msg[i:j])


def test_labeled_arm_keeps_party_and_substance():
    msg = render_user_message(SUBST_CASE, arm="labeled", prompt_version=P5)
    assert "Alternativ A (C): Motförslaget vill att arbetslöshetsförsäkringen" in msg


def test_reservation_falls_back_to_label_without_substance(monkeypatch):
    # With the substance layer empty (CI, or a case it hasn't covered), the opaque
    # label is kept, never dropped.
    from aidag import promptgen

    monkeypatch.setattr(promptgen, "_reservations_layer", lambda: {})
    msg = render_user_message(CASE, arm="anonymous", prompt_version=P5)
    assert "Alternativ A: Reservation 1" in msg


def test_coarse_time():
    assert coarse_time("2023-06-07") == "juni 2023"


def test_omvarld_required_in_schema():
    from aidag.promptgen import DECISION_SCHEMA

    assert "omvarld" in DECISION_SCHEMA["required"]
    om = DECISION_SCHEMA["properties"]["omvarld"]
    assert om["required"] == ["paverkar", "faktorer"]


def test_worldstate_block_point_in_time_and_leakfree():
    from aidag.worldstate import available
    from aidag.promptgen import render_worldstate_block

    if not available():
        import pytest

        pytest.skip("worldstate datasets not built")
    block = render_worldstate_block("2023-06-07")
    assert "styrränta" in block.lower()
    for pattern in FORBIDDEN_PATTERNS:
        assert not re.search(pattern, block), f"leak {pattern} in worldstate block"
    assert "Riksdagen beslutade" not in block and "riksdagen röstade" not in block.lower()
    assert "opinion" not in block.lower()  # polls stay out of prompts


def test_no_poll_data_in_prompt():
    # methodology: agents must follow party plans, never adjust to ratings —
    # opinion-poll numbers live in the KB for the website but never in prompts
    msg = render_user_message(CASE, arm="anonymous")
    assert "Opinionsläge" not in msg
    assert "opinionsmätning" not in msg.lower()
    assert "väljarstöd" not in msg.lower()


def test_scrub_text_replaces_docrefs_and_dates():
    out = scrub_text("bifaller proposition 2022/23:85 beslutad 2023-06-01", arm="anonymous")
    assert "2022/23:85" not in out and "2023-06-01" not in out


def test_tido_gating():
    assert tido_applies("M", "2023-01-01")
    assert tido_applies("SD", "2023-01-01")
    assert not tido_applies("M", "2022-10-01")  # before Tidöavtalet
    assert not tido_applies("S", "2023-01-01")  # opposition never


@pytest.mark.parametrize("code", ["S", "M", "SD", "V"])
def test_system_blocks_contain_manifesto_and_cache_marker(code):
    blocks = build_system_blocks(code, "2023-06-07", prompt_version=P5)
    assert any("<valmanifest_2022>" in b["text"] for b in blocks)
    assert blocks[-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    has_tido = any("<tidoavtalet>" in b["text"] for b in blocks)
    assert has_tido == (code in {"M", "SD"})


def test_system_blocks_byte_identical_within_party():
    # cache prefix guarantee: same party + same Tidö era => identical bytes
    a = build_system_blocks("M", "2023-06-07")
    b = build_system_blocks("M", "2024-11-01")
    assert a == b


class TestP6StanceIsGrounded:
    """A p6 `hallning` is a stance on the counter-proposal's DEMAND, and
    `derive_rost` turns it into a published vote by applying a sign. Both only
    mean something if the agent was shown a counter-proposal — see
    `promptgen.p6_decidable` for what the silent p5 fallback cost on full-v4.
    """

    # The p6 <arende> is built from the committed casemeta brief keyed on
    # votering_id, NOT from this dict's `alternatives` — so an undecidable
    # fixture needs an id casemeta does not cover. Stripping `alternatives`
    # alone leaves the real brief, Nej side and all, in the render.
    NO_MOTFORSLAG = {
        **CASE,
        "votering_id": "00000000-0000-4000-8000-000000000000",
        "alternatives": [
            {"alt_id": "utskottet", "text": "Bifall till propositionen", "source_partier": []}
        ],
    }

    def test_case_with_only_utskottet_is_undecidable_under_p6(self):
        assert not p6_decidable(self.NO_MOTFORSLAG, "anonymous")

    def test_undecidable_render_is_the_p5_shape(self):
        # Why the predicate is needed at all: the render does not fail, it
        # QUIETLY answers a different question. The p6 schema has no `rost`, so
        # this closing line has no field to land in but `hallning`.
        msg = render_user_message(self.NO_MOTFORSLAG, "anonymous", "p6")
        assert "Motförslag i voteringen:" not in msg
        assert msg.rstrip().endswith("enligt sina egna dokument?")
        assert "rost" not in DECISION_SCHEMA_P6["properties"]

    def test_predicate_and_render_cannot_drift_apart(self):
        # The invariant the run-level check in `simulate.verify_run` relies on:
        # undecidable is exactly "the rendered p6 prompt carries no motförslag".
        # Asserted on the real corpus, since the p6 <arende> is built from the
        # committed casemeta brief rather than from the case dict.
        import polars as pl

        from aidag.config import PROCESSED_DIR

        try:
            cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
        except FileNotFoundError:
            pytest.skip("cases.parquet absent — run locally")
        for c in cases.iter_rows(named=True):
            msg = render_user_message(c, "anonymous", "p6")
            assert p6_decidable(c, "anonymous") == ("Motförslag i voteringen:" in msg), (
                c["votering_id"]
            )

    def test_sign_is_a_sign_not_a_relabelling(self):
        assert derive_rost("stodjer") == "Nej"
        assert derive_rost("avvisar") == "Ja"
