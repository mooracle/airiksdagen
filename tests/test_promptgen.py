"""Golden tests for prompt rendering: leakage guards and Tidö gating.

These are the checks behind `aidag verify prompts` — if any of them fail, the
no-future-information / no-identifier guarantees of the methodology are broken.
"""

import re

import pytest

from aidag.promptgen import (
    FORBIDDEN_PATTERNS,
    build_system_blocks,
    coarse_time,
    render_user_message,
    scrub_text,
    tido_applies,
)

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
    msg = render_user_message(MFL_CASE, arm="labeled")
    assert "(MP)" in msg and "Annika Hirvonen" in msg


def test_labeled_arm_keeps_reservation_party():
    msg = render_user_message(CASE, arm="labeled")
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
    msg = render_user_message(SUBST_CASE, arm="anonymous")
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
    msg = render_user_message(SUBST_CASE, arm="labeled")
    assert "Alternativ A (C): Motförslaget vill att arbetslöshetsförsäkringen" in msg


def test_reservation_falls_back_to_label_without_substance(monkeypatch):
    # With the substance layer empty (CI, or a case it hasn't covered), the opaque
    # label is kept, never dropped.
    from aidag import promptgen

    monkeypatch.setattr(promptgen, "_reservations_layer", lambda: {})
    msg = render_user_message(CASE, arm="anonymous")
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
    blocks = build_system_blocks(code, "2023-06-07")
    assert any("<valmanifest_2022>" in b["text"] for b in blocks)
    assert blocks[-1].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    has_tido = any("<tidoavtalet>" in b["text"] for b in blocks)
    assert has_tido == (code in {"M", "SD"})


def test_system_blocks_byte_identical_within_party():
    # cache prefix guarantee: same party + same Tidö era => identical bytes
    a = build_system_blocks("M", "2023-06-07")
    b = build_system_blocks("M", "2024-11-01")
    assert a == b
