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
    assert "Alternativ A: Reservation 1" in msg
    assert "(C)" not in msg


def test_labeled_arm_keeps_reservation_party():
    msg = render_user_message(CASE, arm="labeled")
    assert "Alternativ A (C):" in msg


def test_coarse_time():
    assert coarse_time("2023-06-07") == "juni 2023"


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
