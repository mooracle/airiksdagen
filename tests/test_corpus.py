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
    # data/processed is gitignored and re-fetchable; skip these data-dependent
    # leak checks when it hasn't been built (e.g. a clean CI checkout).
    path = PROCESSED_DIR / "cases.parquet"
    if not path.exists():
        pytest.skip(f"{path} not built (run: uv run aidag build-cases)")
    return pl.read_parquet(
        path, columns=["votering_id", "datum", "rm"]
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


class TestSiteCorpusMatchesWhatAgentsRead:
    """The /dokument/ pages tell the reader "this is what the AI agents read".

    That is only true of `normalize()` output. The raw file still holds the
    artifacts it strips, and one of them — an undecodable display-font heading in
    mp-2013's PDF — sits mid-sentence, splitting a real citation in two so its
    deep-link could not resolve. export_site.export_corpus() therefore exports the
    normalized text, and this pins that.
    """

    def _pairs(self):
        from aidag.config import CORPUS_DIR, SITE_DATA_DIR

        out = SITE_DATA_DIR / "corpus"
        if not out.exists():
            pytest.skip(f"{out} not exported (run: uv run aidag export-site)")
        for raw_path in sorted(CORPUS_DIR.glob("*.txt")):
            site_path = out / raw_path.name
            if site_path.exists():
                yield raw_path, site_path

    def test_every_exported_document_is_normalized(self):
        import re

        seen = 0
        for raw_path, site_path in self._pairs():
            raw = raw_path.read_text(encoding="utf-8").lstrip("﻿")
            served = site_path.read_text(encoding="utf-8")
            # the provenance comment is re-attached for the page's source credit
            body = re.sub(r"^<!--.*?-->\n", "", served, flags=re.S).strip()
            assert body == corpus.normalize(raw), f"{site_path.name} is not normalize() output"
            seen += 1
        assert seen >= 40, f"expected the full corpus, exported only {seen}"

    def test_no_broken_font_or_ligature_residue_reaches_the_site(self):
        for _raw_path, site_path in self._pairs():
            served = site_path.read_text(encoding="utf-8")
            body = served.split("\n", 1)[1] if served.startswith("<!--") else served
            assert not any(c in body for c in "ﬀﬁﬂﬃﬄ"), site_path.name
            assert "­" not in body and "﻿" not in body, site_path.name
            for line in body.splitlines():
                assert not corpus._broken_font_line(line), f"{site_path.name}: {line!r}"
