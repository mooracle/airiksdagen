"""Leak guards for the p5 date-gated corpus.

Two ways this can silently corrupt the study, both tested here:

  lookahead   serving a document adopted AFTER the vote. Not hypothetical: MP's
              2025 programme contains "När Sverige anslöt sig till Nato valde
              Miljöpartiet att rösta emot beslutet" — the party stating how it
              voted on a division in this dataset. Shown to that vote, the agent
              reads the answer off the page.

  own motion  serving a party its own shadow budget on the division that votes
              on that budget — the same answer key that reservation authorship
              would be.
"""

import polars as pl
import pytest

from aidag import corpus
from aidag.config import BUDGET_MOTIONS, PARTY_CODES, PARTY_PROGRAMS, PROCESSED_DIR


@pytest.fixture(scope="module")
def cases():
    return pl.read_parquet(
        PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum", "rm"]
    ).to_dicts()


class TestNoLookahead:
    def test_no_document_predates_its_own_vote(self, cases):
        """Every served document was adopted on or before the vote it informs."""
        for c in cases:
            for p in PARTY_CODES:
                if prog := corpus.program_at(p, c["datum"]):
                    assert prog["from"] <= c["datum"], f"{p} programme from the future"
                if b := corpus.budget_at(p, c["rm"], c["datum"]):
                    assert b["from"] <= c["datum"], f"{p} budget from the future"

    def test_a_2023_vote_never_sees_the_2025_programmes(self):
        # the versions adopted mid-term, which describe how parties voted
        assert corpus.program_at("MP", "2023-04-12")["from"] == "2013-01-01"
        assert corpus.program_at("S", "2023-04-12")["from"] == "2013-04-07"
        assert corpus.program_at("V", "2023-04-12")["from"] == "2016-05-08"

    def test_later_votes_do_see_the_newer_programme(self):
        assert corpus.program_at("V", "2026-01-15")["from"] == "2024-05-12"
        assert corpus.program_at("MP", "2026-01-15")["from"] == "2025-10-19"

    def test_the_nato_sentence_is_gated_away_from_the_votes_it_describes(self):
        """The concrete leak this whole mechanism exists to prevent."""
        from aidag.fetch_corpus import program_filename

        later = PARTY_PROGRAMS["MP"][-1]
        text = (corpus.CORPUS_DIR / program_filename("MP", later)).read_text()
        assert "anslöt sig" in text and "Nato" in text, "fixture drifted: sentence gone"
        served_2023 = [k for k, _t, _x in corpus.documents_for("MP", "2023-04-12", "2022/23", "V1", "p5")]
        assert "partiprogram" in served_2023
        prog_2023 = corpus.program_at("MP", "2023-04-12")
        assert prog_2023 != later, "the 2025 programme reached a 2023 vote"


class TestOwnBudgetExcluded:
    def test_excluded_on_the_division_that_votes_on_it(self, cases):
        by_vid = {c["votering_id"]: c for c in cases}
        # FiU1 2022/23 punkt 1 — the chamber votes on the budget alternatives
        hits = [
            c for c in cases
            if corpus.budget_excluded("V", c["rm"], c["votering_id"], c["datum"])
        ]
        assert hits, "V's own budget is never excluded — the leak control is dead"
        for c in hits:
            served = [k for k, _t, _x in corpus.documents_for("V", c["datum"], c["rm"], c["votering_id"], "p5")]
            assert "budgetmotion" not in served, "party served its own budget on its own vote"
            assert by_vid[c["votering_id"]]  # sanity

    def test_still_served_on_unrelated_divisions(self):
        # a spring 2023 vote unrelated to the budget: V keeps its budget motion
        served = [
            k for k, _t, _x in corpus.documents_for("V", "2023-04-12", "2022/23", "NOT-A-BUDGET-VOTE", "p5")
        ]
        assert "budgetmotion" in served

    def test_only_the_four_filing_parties_have_one(self):
        assert {p for p, _rm in BUDGET_MOTIONS} == {"S", "V", "C", "MP"}
        for p in ("M", "KD", "L", "SD"):
            assert corpus.budget_at(p, "2022/23", "2023-04-12") is None


class TestP4Frozen:
    def test_p4_serves_exactly_the_two_original_documents(self):
        assert corpus.docs_for_version("p4") == ("valmanifest", "tidoavtalet")
        served = [k for k, _t, _x in corpus.documents_for("MP", "2023-04-12", "2022/23", "V1", "p4")]
        assert served == ["valmanifest"]
        gov = [k for k, _t, _x in corpus.documents_for("M", "2023-04-12", "2022/23", "V1", "p4")]
        assert gov == ["valmanifest", "tidoavtalet"]

    def test_p4_never_serves_a_programme_or_budget(self, cases):
        for c in cases[:200]:
            for p in PARTY_CODES:
                served = {k for k, _t, _x in corpus.documents_for(p, c["datum"], c["rm"], c["votering_id"], "p4")}
                assert not (served - {"valmanifest", "tidoavtalet"})
