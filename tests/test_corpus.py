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
        """The concrete leak this whole mechanism exists to prevent.

        Asserted on the SERVED text, not on the file: structured extraction
        re-orders multi-column pages, so "the sentence is in mp-2025.txt" is a
        statement about a fixture, while "no 2023 vote is ever shown it" is the
        invariant. Both renditions are checked — the frozen bytes p5 serves and
        the re-extracted file p6 serves — because the sentence surviving
        re-extraction is itself something that could quietly stop being true.
        """
        from aidag.fetch_corpus import program_filename

        later = PARTY_PROGRAMS["MP"][-1]
        name = program_filename("MP", later)
        for path in (corpus.CORPUS_DIR / name, corpus.FROZEN_DIR / name):
            text = path.read_text()
            assert "anslöt sig" in text and "Nato" in text, f"{path.name}: sentence gone"

        for version in ("p5", "p6"):
            served = corpus.documents_for("MP", "2023-04-12", "2022/23", "V1", version)
            assert "partiprogram" in [k for k, _t, _x in served]
            assert not any("anslöt sig" in x for _k, _t, x in served)
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


class TestPreP6ReadsTheFrozenCorpus:
    """Structured extraction rewrote 23 of the 40 corpus documents.

    full-v2 (p4) and full-v3 (p5, 6,563 decisions, in the repo) were generated
    from the bytes that stood before it, and `verify simulate` checks that every
    citation is an exact substring of what the agent was shown. Serving them the
    re-extracted text would fail citations that were never wrong — so everything
    below p6 reads `data/corpus/frozen/` and p6 reads the regenerated file.
    """

    def test_every_corpus_document_is_frozen(self):
        """All 40, not only the 23 that moved: an unconditional rule cannot be
        wrong about which files a later change touches."""
        live = {p.name for p in corpus.CORPUS_DIR.glob("*.txt")}
        frozen = {p.name for p in corpus.FROZEN_DIR.glob("*.txt")}
        assert live == frozen and len(live) == 40

    def test_the_regeneration_really_moved_the_bytes(self):
        """Without this the routing tests below are vacuously true."""
        from aidag.extract_corpus import cited_slugs

        moved = [
            s for s in cited_slugs()
            if (corpus.CORPUS_DIR / f"{s}.txt").read_bytes()
            != (corpus.FROZEN_DIR / f"{s}.txt").read_bytes()
        ]
        assert len(moved) == 23

    def test_p5_is_served_the_frozen_text(self):
        served = dict(
            (k, x) for k, _t, x in
            corpus.documents_for("KD", "2023-04-12", "2022/23", "V1", "p5")
        )
        frozen = (corpus.FROZEN_DIR / "valmanifest-2022-kd.txt").read_text().lstrip("﻿")
        assert served["valmanifest"] == corpus.normalize(frozen)
        live = (corpus.CORPUS_DIR / "valmanifest-2022-kd.txt").read_text()
        assert served["valmanifest"] != corpus.normalize(live)

    def test_p6_is_served_the_regenerated_text(self):
        served = dict(
            (k, x) for k, _t, x in
            corpus.documents_for("KD", "2023-04-12", "2022/23", "V1", "p6")
        )
        live = (corpus.CORPUS_DIR / "valmanifest-2022-kd.txt").read_text()
        assert served["valmanifest"] == corpus.normalize(live)

    def test_p4_is_served_the_frozen_bytes_raw(self):
        """p4 never normalized at all — its corpus is the file, verbatim."""
        served = dict(
            (k, x) for k, _t, x in
            corpus.documents_for("KD", "2023-04-12", "2022/23", "V1", "p4")
        )
        frozen = (corpus.FROZEN_DIR / "valmanifest-2022-kd.txt").read_text()
        assert served["valmanifest"] == frozen.lstrip("﻿").strip()

    def test_the_out_of_scope_documents_read_the_same_either_way(self):
        """Tidöavtalet and the budgetmotioner were not re-extracted."""
        for version in ("p4", "p5"):
            served = dict(
                (k, x) for k, _t, x in
                corpus.documents_for("M", "2023-04-12", "2022/23", "V1", version)
            )
            live = (corpus.CORPUS_DIR / "tidoavtalet-2022.txt").read_text().strip()
            expected = corpus.normalize(live) if version == "p5" else live
            assert served["tidoavtalet"] == expected


class TestVersionsOrderNumerically:
    """`p10` must not read as older than `p6`.

    Every gate in the pipeline is a version comparison, and comparing the raw
    strings puts `"p10" < "p6"`. The first two-digit version would then be served
    `data/corpus/frozen/`, prompted with the pre-p6 schema, skipped by
    `migrate-quotes` and `build-anchors` and denied the known-unrecovered
    allowlist — none of it raising, and `verify simulate` still green, because it
    would be checked against the same frozen bytes it was prompted with.
    """

    def test_two_digit_versions_sort_after_single_digit_ones(self):
        from aidag.config import version_ge

        assert version_ge("p10", "p6") and version_ge("p10", "p9")
        assert not version_ge("p6", "p10")
        assert version_ge("p6", "p6") and not version_ge("p5", "p6")

    def test_the_mock_version_still_sorts_below_every_real_one(self):
        """`mock.PROMPT_VERSION` relies on this to be served the frozen corpus."""
        from aidag.config import version_ge
        from aidag.mock import PROMPT_VERSION as MOCK_VERSION

        assert not version_ge(MOCK_VERSION, "p4")
        assert not version_ge(MOCK_VERSION, "p6")

    def test_a_two_digit_version_is_routed_like_the_newest_corpus(self):
        """The routing itself, not just the comparison helper."""
        assert corpus.docs_for_version("p10") == corpus.DOCS_P6
        served = {
            k: x
            for k, _t, x in corpus.documents_for("KD", "2023-04-12", "2022/23", "V1", "p10")
        }
        live = (corpus.CORPUS_DIR / "valmanifest-2022-kd.txt").read_text()
        assert served["valmanifest"] == corpus.normalize(live)

    def test_a_two_digit_run_is_not_skipped_by_the_citation_passes(self):
        from aidag.config import version_ge
        from aidag.migrate_quotes import MIGRATED_FROM

        assert version_ge("p10", MIGRATED_FROM)


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


class TestMockReadsTheSameBytesVerifyServesItBack:
    """`mock-v1` cites real manifesto substrings, and has to cite the right copy.

    The snippets and `verify simulate` ask one question twice: which bytes does
    a decision at this `prompt_version` read? `mock.PROMPT_VERSION` is "mock",
    which sorts below "p6", so `documents_for()` serves it `data/corpus/frozen/`.
    Taking the snippet from `data/corpus/` instead would draw it from the
    re-extracted p6 bytes — and for the parties whose manifesto moved, the mock
    run's own citations then read as hallucinated.
    """

    def test_the_mock_version_selects_the_frozen_corpus(self):
        from aidag import mock

        assert mock.PROMPT_VERSION < "p6"

    def test_every_partys_snippet_is_verbatim_in_what_verify_serves(self):
        from aidag import mock
        from aidag.config import PARTY_CODES

        frozen = mock.PROMPT_VERSION < "p6"
        for p in PARTY_CODES:
            name = f"valmanifest-2022-{p.lower()}.txt"
            text = corpus._text(name, frozen=frozen)
            snippet = " ".join(" ".join(text.split()).split(" ")[100:120])
            assert snippet, name
            served = dict(
                (kind, body)
                for kind, _tag, body in corpus.documents_for(
                    p, "2023-01-01", "2022/23", "mock", mock.PROMPT_VERSION
                )
            )["valmanifest"]
            assert snippet in " ".join(served.split()), f"{p}: snippet is not in the served bytes"
