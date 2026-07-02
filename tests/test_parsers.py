import json
import zipfile
from pathlib import Path

import polars as pl

from aidag.build_cases import (
    build_alternatives,
    build_party_positions,
    extract_forslag,
    extract_uppgift,
)
from aidag.fetch_votes import FILENAME_RE
from aidag.verify_crosscheck import parse_summary_table

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture_ds() -> dict:
    return json.loads((FIXTURES / "HA01AU10.json").read_text())["dokumentstatus"]


def load_fixture_votes() -> list[dict]:
    path = next(FIXTURES.glob("HA01AU10-1-*.json"))
    return json.loads(path.read_text())["dokvotering"]["votering"]


def test_filename_regex():
    m = FILENAME_RE.match("HA01AU10-1-91110125-72B3-4C4F-8B1A-584C5616EF08.json")
    assert m and m["dok_id"] == "HA01AU10" and m["punkt"] == "1"


def test_extract_forslag_maps_votering_ids():
    forslag = extract_forslag(load_fixture_ds())
    assert "91110125-72B3-4C4F-8B1A-584C5616EF08" in forslag
    uf = forslag["91110125-72B3-4C4F-8B1A-584C5616EF08"]
    assert uf["punkt"] == "1"
    assert "arbetslöshetsförsäkring" in uf["forslag"]


def test_alternatives_include_reservation_with_party():
    ds = load_fixture_ds()
    uf = extract_forslag(ds)["91110125-72B3-4C4F-8B1A-584C5616EF08"]
    alts = build_alternatives(uf, ds)
    assert alts[0]["alt_id"] == "utskottet"
    reservations = [a for a in alts if a["alt_id"].startswith("res-")]
    assert reservations and reservations[0]["source_partier"] == ["C"]


def test_uppgift_notis_is_post_decision():
    # regression guard: notis contains the outcome and must stay out of prompts
    notis = extract_uppgift(load_fixture_ds(), "notis")
    assert "Riksdagen sa ja" in notis


def test_summary_table_parses_counts():
    uf = extract_forslag(load_fixture_ds())["91110125-72B3-4C4F-8B1A-584C5616EF08"]
    table = parse_summary_table(uf)
    assert table is not None
    assert table["S"] == (94, 0, 0, 13)


def test_party_positions_majority_and_cohesion():
    votes = pl.DataFrame(load_fixture_votes()).with_columns(
        pl.col("votering_id").str.to_uppercase()
    )
    votes = votes.filter(pl.col("parti") != "-")
    pos = build_party_positions(votes)
    s = pos.filter(pl.col("parti") == "S").to_dicts()[0]
    assert s["position"] in ("Ja", "Nej", "Avstår", "Frånvarande")
    assert 0 < s["cohesion"] <= 1.0
    assert s["seats"] == s["n_ja"] + s["n_nej"] + s["n_avstar"] + s["n_franvarande"]
