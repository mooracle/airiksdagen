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


class TestMotionDemands:
    """`casemeta.motion_demands` recovers the Nej side on a punkt with no
    reservation — the gap that left 10 full-v4 cases with a p6 stance and nothing
    to take a stance on (see `promptgen.p6_decidable`).
    """

    # The shape these betänkanden actually have: a "Motionerna" section whose
    # entries open with "I motion <nr>" / "I kommittémotion <nr>", closed by the
    # committee's own answer.
    PLAIN = (
        "Utskottets förslag till riksdagsbeslut … Motionerna "
        "I kommittémotion 2025/26:4176 av Emma Berginger m.fl. (MP) föreslås att riksdagen "
        "avslår regeringens förslag till lag om ändring i 2 § lagen (2020:782). "
        "I motion 2025/26:4171 av Lorena Delgado Varas m.fl. (-) föreslås avslag i dess helhet. "
        "Utskottets ställningstagande Utskottet anser att förslagen är ändamålsenliga."
    )

    def test_recovers_the_named_motion_only(self):
        from aidag.casemeta import motion_demands

        case = {
            "forslag_text": (
                "Riksdagen antar regeringens förslag … Därmed bifaller riksdagen "
                "proposition 2025/26:254 punkt 4 i denna del och avslår motion "
                "2025/26:4176 av Emma Berginger m.fl. (MP) yrkande 1."
            )
        }
        got = motion_demands(case, self.PLAIN)
        assert [d["motion"] for d in got] == ["2025/26:4176"]
        assert got[0]["alt_id"] == "mot-4176"

    def test_the_proposition_is_not_the_nej_side(self):
        # 2025/26:254 is what the committee is FOR and is named in the same
        # sentence. Reading the ids out of `forslag_text` alone would take it for
        # a counter-proposal and invert the case; the "I motion <nr>" test in the
        # fulltext is what rules it out.
        from aidag.casemeta import motion_demands

        case = {"forslag_text": "… bifaller riksdagen proposition 2025/26:254 … avslår motion 2025/26:4176 …"}
        assert all(d["motion"] != "2025/26:254" for d in motion_demands(case, self.PLAIN))

    def test_body_stops_at_the_committee_answer(self):
        from aidag.casemeta import motion_demands

        case = {"forslag_text": "avslår motion 2025/26:4171"}
        body = motion_demands(case, self.PLAIN)[0]["body"]
        assert "avslag i dess helhet" in body
        assert "ställningstagande" not in body.lower()

    def test_a_bundle_point_has_no_single_demand(self):
        # "Motioner som bereds förenklat": the committee turns down 17-20 unrelated
        # yrkanden at once and `forslag_text` names no motion. There is no one
        # demand to state, so the point stays undecidable under p6 rather than
        # getting a synthesised stand-in.
        from aidag.casemeta import motion_demands

        case = {
            "forslag_text": (
                "Riksdagen avslår de motionsyrkanden som finns upptagna under denna punkt "
                "i utskottets förteckning över avstyrkta motionsyrkanden."
            )
        }
        assert motion_demands(case, self.PLAIN) == []


class TestBundleYrkanden:
    """`casemeta.bundle_yrkanden` recovers what a "Motioner som bereds förenklat"
    punkt turns down. It exists to make the p6 exclusion of those punkter
    evidence-based: the demands are fully recoverable, and recovering them is what
    shows the punkt asks for a thing and its opposite in the same vote.
    """

    PLAIN = (
        "Innehåll Bilaga 1 Förteckning över behandlade förslag Bilaga 2 Motionsyrkanden "
        "som avstyrks av utskottet "
        "Utskottets ställningstagande Utskottet avstyrker yrkandena. "
        "Bilaga 1 Förteckning över behandlade förslag Motioner från allmänna motionstiden "
        "2024/25:135 av Magnus Jacobsson (KD): Riksdagen ställer sig bakom det som anförs i "
        "motionen om att verka för att UNRWA avvecklas och tillkännager detta för regeringen. "
        "2024/25:3168 av Anna Lasses m.fl. (C): 12. Riksdagen ställer sig bakom det som anförs "
        "i motionen om att Sveriges stöd till Palestina, inklusive stöd till UNRWA, bör "
        "fortsätta och utvecklas. "
        "Bilaga 2 Motionsyrkanden som avstyrks av utskottet Motion Motionärer Yrkanden "
        "7. Motioner som bereds förenklat 2024/25:135 Magnus Jacobsson (KD) "
        "2024/25:3168 Anna Lasses m.fl. (C) 12 "
        "8. Något annat Riksdagen avslår motionerna."
    )

    def test_recovers_the_punkt_bundle_verbatim(self):
        from aidag.casemeta import bundle_yrkanden

        got = bundle_yrkanden({"punkt": 7}, self.PLAIN)
        assert [g["motion"] for g in got] == ["2024/25:135", "2024/25:3168"]
        assert "UNRWA avvecklas" in got[0]["text"]

    def test_the_bundle_demands_a_thing_and_its_opposite(self):
        # The whole reason these punkter stay undecidable under p6. `hallning` asks
        # whether the party's plan supports what the counter-proposal demands; here
        # the bundle demands both that UNRWA be wound up and that support to it
        # continue, so no single answer exists. If a future change ever makes one of
        # these decidable, this is the assumption to revisit.
        from aidag.casemeta import bundle_yrkanden

        texts = " ".join(g["text"] for g in bundle_yrkanden({"punkt": 7}, self.PLAIN))
        assert "UNRWA avvecklas" in texts
        assert "stöd till UNRWA, bör fortsätta" in texts

    def test_headings_in_the_table_of_contents_are_not_the_appendix(self):
        # Both Bilaga headings also appear in the document's own ToC, so the parser
        # takes the LAST occurrence. Slicing on the first would read the catalogue
        # out of an empty span and recover nothing.
        from aidag.casemeta import bundle_yrkanden

        assert bundle_yrkanden({"punkt": 7}, self.PLAIN)

    def test_a_punkt_with_no_bundle_returns_nothing(self):
        from aidag.casemeta import bundle_yrkanden

        assert bundle_yrkanden({"punkt": 3}, self.PLAIN) == []
