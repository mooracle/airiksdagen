"""Moving 77,718 committed quotes onto a re-extracted corpus, without losing any.

The fixtures are real corpus documents and real damage patterns, because every
quote this pass has to move was broken by a real-document quirk: a word split
across a line (`omfatt ning`), a hyphen the old extractor dropped (`energioch`),
a hyphen/en-dash swap. Synthetic text cannot reproduce the thing that makes this
hard, which is that the *right* answer sits one word away from three wrong ones.

The write path is tested for what it refuses as much as for what it does. This
module edits `data/results/simulations/`, which is the committed research record:
a quote it cannot place must come out of the run byte-identical to how the agent
wrote it, and a second run must be a no-op.
"""

import json

import pytest

from aidag import corpus
from aidag import migrate_quotes as mq
from aidag.config import CORPUS_DIR, PROCESSED_DIR
from aidag.simulate import _normalize_ws


def _index(slug: str) -> mq.DocIndex:
    if not (CORPUS_DIR / f"{slug}.txt").exists():
        pytest.skip(f"{slug} not extracted (run: uv run aidag extract-corpus)")
    return mq.index_for(slug)


@pytest.fixture(scope="module")
def kd2015():
    return _index("partiprogram-kd-2015")


@pytest.fixture(scope="module")
def kd_manifest():
    return _index("valmanifest-2022-kd")


def _sentence(idx: mq.DocIndex, min_words: int = 14) -> str:
    """A real block from the document, long enough to locate unambiguously."""
    for lo, hi in idx.spans:
        text = idx.served[lo:hi]
        if len(text.split()) >= min_words and text[0].isupper():
            return text
    raise AssertionError("no usable block")


def _break_a_word(sentence: str) -> str:
    """The defect the old extractor left: a word split by a stray space.

    The first word long enough that the break survives whitespace collapsing —
    splitting `på` yields a double space and `locate` would call it exact.
    """
    words = sentence.split()
    for i, w in enumerate(words):
        if len(w) >= 8 and w.isalpha():
            words[i] = w[:4] + " " + w[4:]
            return " ".join(words)
    raise AssertionError("no word long enough to break")


def _a_real_votering() -> str:
    """A votering_id `run()` can date, so `partiprogram` resolves to an edition.

    `run()` reads the date from cases.parquet rather than from the decision, and
    an unknown id dates to "" — which resolves to no programme at all and would
    quietly turn the write-path tests into a test of the skip branch.
    """
    import polars as pl

    path = PROCESSED_DIR / "cases.parquet"
    if not path.exists():
        pytest.skip(f"{path} not built (run: uv run aidag build-cases)")
    rows = pl.read_parquet(path, columns=["votering_id", "datum"]).to_dicts()
    for r in rows:
        if r["datum"] >= "2022-10-01":
            return r["votering_id"]
    pytest.skip("no case in the term")


def _decision(quotes, parti="KD", document="partiprogram", flags=None, vid="TEST-VID"):
    return {
        "votering_id": vid,
        "parti": parti,
        "prompt_version": "p6",
        "flags": list(flags or []),
        "citations": [{"document": document, "quote": q, "princip": "p"} for q in quotes],
    }


class TestResolveSlug:
    """A citation names a document CLASS. Which edition it meant is a date."""

    def test_a_kd_vote_in_2023_resolves_to_the_2015_programme(self):
        assert mq.resolve_slug("partiprogram", "KD", "2023-04-12") == "partiprogram-kd-2015"

    def test_a_kd_vote_in_2026_resolves_to_the_2025_programme(self):
        assert mq.resolve_slug("partiprogram", "KD", "2026-01-15") == "partiprogram-kd-2025"

    def test_the_manifesto_is_the_same_edition_on_every_date(self):
        for datum in ("2022-11-01", "2026-01-15"):
            assert mq.resolve_slug("valmanifest", "M", datum) == "valmanifest-2022-m"

    def test_classes_this_pass_never_touches_resolve_to_nothing(self):
        """Budgetmotioner and Tidöavtalet were not re-extracted, so nothing moved."""
        for kind in ("budgetmotion", "tidoavtalet", "hallucinerat_dokument"):
            assert mq.resolve_slug(kind, "S", "2023-04-12") is None

    def test_a_party_with_no_standing_programme_resolves_to_nothing(self):
        assert corpus.program_at("KD", "2014-01-01") is None
        assert mq.resolve_slug("partiprogram", "KD", "2014-01-01") is None

    def test_the_resolved_document_is_the_one_the_agent_was_served(self):
        """The guarantee the fast path trades `documents_for()` away for.

        Resolution here goes through `program_at()` for speed — 77,718 citations
        against 23 documents. That is only safe while it lands on exactly the
        text `documents_for()` serves, which is what verify compares against.
        """
        for parti, datum in (("KD", "2023-04-12"), ("KD", "2026-01-15"), ("V", "2026-01-15")):
            served = {
                kind: text
                for kind, _tag, text in corpus.documents_for(parti, datum, "2022/23", "X", "p6")
            }
            for kind, text in served.items():
                slug = mq.resolve_slug(kind, parti, datum)
                assert slug is not None, (parti, datum, kind)
                assert _index(slug).served == _normalize_ws(text)


class TestLocate:
    def test_a_quote_already_in_the_corpus_is_left_alone(self, kd2015):
        m = kd2015.locate(_sentence(kd2015))
        assert m.how == "exact"
        assert m.text == ""

    def test_whitespace_is_collapsed_before_the_comparison(self, kd2015):
        """Agents re-wrap what they quote; verify compares collapsed too."""
        assert kd2015.locate("  \n ".join(_sentence(kd2015).split())).how == "exact"

    def test_a_word_broken_by_the_old_extractor_is_repaired(self, kd2015):
        original = _sentence(kd2015)
        m = kd2015.locate(_break_a_word(original))
        assert m.how == "fuzzy"
        assert m.text == original

    def test_the_span_does_not_bleed_into_the_neighbouring_block(self, kd2015):
        """`best_span` scores fixed widths, so a closed-up word makes it overshoot."""
        original = _sentence(kd2015)
        damaged = _break_a_word(_break_a_word(original))
        assert damaged.count(" ") == original.count(" ") + 2
        assert kd2015.locate(damaged).text == original

    def test_a_quote_that_is_not_in_the_document_fails(self, kd2015):
        m = kd2015.locate(
            "Riksdagen bör omedelbart avskaffa alla kommuner och ersätta dem med "
            "ett landsomfattande lotteri om skattemedlen."
        )
        assert m.how == "failed"
        assert m.text == ""

    def test_an_accepted_span_is_verbatim_in_the_served_document(self, kd2015):
        """The invariant that makes `verify simulate` green after this pass."""
        m = kd2015.locate(_break_a_word(_sentence(kd2015)))
        assert m.how == "fuzzy"
        assert _normalize_ws(m.text) in kd2015.served

    def test_a_quote_sharing_no_word_with_the_document_fails_without_scanning_it(
        self, kd2015
    ):
        """The prefilter finding nothing is a verdict, not a reason to scan.

        `best_span` over a 172-page programme is the multi-minute operation the
        index exists to avoid; reaching for it here would spend it to conclude
        what the empty candidate set already said.
        """
        quote = "zzqx wvbb kkjl mmpr"
        assert kd2015.candidates(quote) == []
        m = kd2015.locate(quote)
        assert (m.how, m.text, m.ratio) == ("failed", "", 0.0)

    def test_an_empty_quote_fails_rather_than_scanning_the_document(self, kd2015):
        m = kd2015.locate("")
        assert m.how == "failed"

    def test_the_threshold_is_the_repair_threshold(self):
        from aidag import repair

        assert mq.THRESHOLD == repair.THRESHOLD


class TestRefine:
    def test_it_trims_a_bleeding_edge(self):
        window = "relationer Vi vill se ett robust samhälle som klarar kriser . Nästa mening"
        span, ratio = mq._refine("Vi vill se ett robust samhälle som klarar kriser .", window)
        assert span == "Vi vill se ett robust samhälle som klarar kriser ."
        assert ratio == 1.0

    def test_it_only_ever_moves_to_a_better_score(self):
        window = "alfa beta gamma delta epsilon zeta eta theta iota kappa lambda my"
        quote = "gamma delta epsilon zeta eta theta"
        from aidag.repair import best_span

        _, coarse = best_span(quote, window)
        _, refined = mq._refine(quote, window)
        assert refined >= coarse

    def test_a_window_with_nothing_to_match_returns_the_coarse_result(self):
        span, ratio = mq._refine("ingenting alls här", "")
        assert span == ""
        assert ratio == 0.0


def _listed() -> dict:
    """One real entry from the committed allowlist, for the party M's manifesto.

    M carries 10 of the 12: pages where a figure's heading sits inside a
    two-column text block, which is the shape the whole list is about.
    """
    payload = json.loads((CORPUS_DIR / "known-unrecovered.json").read_text())
    for q in payload["quotes"]:
        if q["document"] == "valmanifest-2022-m":
            return q
    return payload["quotes"][0]


class TestKnownUnrecovered:
    """The allowlist is a decision, not a label."""

    def test_it_loads_as_collapsed_slug_quote_pairs(self):
        listed = _listed()
        known = mq.known_unrecovered()
        assert (listed["document"], _normalize_ws(listed["quote"])) in known
        assert all(len(k) == 2 for k in known)

    def test_the_matcher_on_its_own_would_place_a_listed_quote(self):
        """Why the list has to outrank the score rather than merely label it.

        These score over the 0.75 bar and return the neighbouring column with a
        rotated margin stamp or a chart source spliced in — verbatim in the
        served document, and not what the party wrote.
        """
        listed = _listed()
        m = _index(listed["document"]).locate(listed["quote"])
        assert m.how in ("fuzzy", "failed")
        if m.how == "fuzzy":
            assert m.text != _normalize_ws(listed["quote"])

    def test_a_listed_quote_is_refused_before_the_matcher_sees_it(self):
        listed = _listed()
        key = (listed["document"], _normalize_ws(listed["quote"]))
        d = _decision([listed["quote"]], parti="M", document="valmanifest")
        counts = mq.migrate_decision(d, "2023-04-12", known={key})
        assert counts["failed"] == 1
        assert d["citations"][0]["quote"] == listed["quote"]
        assert "quote_fore_migrering" not in d["citations"][0]
        assert d["flags"] == ["citat_ej_migrerat"]

    def test_the_refusal_is_recorded_at_ratio_zero(self):
        """A score would read as 'nearly good enough' for a span never used."""
        assert mq.KNOWN_FAILURE.how == "failed"
        assert mq.KNOWN_FAILURE.text == ""
        assert mq.KNOWN_FAILURE.ratio == 0.0

    def test_an_absent_allowlist_refuses_rather_than_reading_as_empty(self, tmp_path, monkeypatch):
        """Failing open here is the one failure mode with no other error path.

        An empty set reads to both callers as 'not listed, offer it to the
        matcher', and all 12 score over the bar against the neighbouring column
        — so repair would rewrite every one and stamp `citat_korrigerat`, booking
        an extraction failure as the model paraphrasing. Both outputs are
        verbatim substrings, so `verify simulate` and `citation-audit` stay green.
        """
        monkeypatch.setattr(mq, "CORPUS_DIR", tmp_path)
        with pytest.raises(FileNotFoundError, match="known-unrecovered.json"):
            mq.known_unrecovered()


class TestMigrateDecision:
    def test_an_exact_quote_is_passed_through_untouched(self, kd2015):
        quote = _sentence(kd2015)
        d = _decision([quote])
        counts = mq.migrate_decision(d, "2023-04-12")
        assert counts["exact"] == 1
        assert d["citations"][0] == {"document": "partiprogram", "quote": quote, "princip": "p"}
        assert d["flags"] == []

    def test_a_repaired_quote_gets_a_sidecar_and_a_flag(self, kd2015):
        original = _sentence(kd2015)
        d = _decision([_break_a_word(original)])
        counts = mq.migrate_decision(d, "2023-04-12")
        assert counts["fuzzy"] == 1
        c = d["citations"][0]
        assert c["quote"] == original
        assert c["quote_fore_migrering"] == _break_a_word(original)
        assert d["flags"] == ["citat_migrerat"]

    def test_a_quote_that_cannot_be_placed_is_left_exactly_as_written(self, kd2015):
        junk = "Detta är en mening som aldrig har stått i något partiprogram alls."
        d = _decision([junk])
        counts = mq.migrate_decision(d, "2023-04-12")
        assert counts["failed"] == 1
        assert d["citations"][0]["quote"] == junk
        assert "quote_fore_migrering" not in d["citations"][0]
        assert "quote_ej_verifierad" not in d["citations"][0]
        assert d["flags"] == ["citat_ej_migrerat"]

    def test_an_already_blanked_quote_is_skipped(self):
        d = _decision([""])
        d["citations"][0]["quote_ej_verifierad"] = "vad modellen skrev"
        counts = mq.migrate_decision(d, "2023-04-12")
        assert counts["blank"] == 1
        assert d["citations"][0]["quote"] == ""
        assert d["flags"] == []

    def test_a_citation_to_an_untouched_document_class_is_skipped(self):
        d = _decision(["vad som helst"], document="budgetmotion")
        counts = mq.migrate_decision(d, "2023-04-12")
        assert counts["out_of_scope"] == 1
        assert d["flags"] == []

    def test_an_in_scope_citation_that_dates_no_edition_is_not_counted_as_out_of_scope(
        self,
    ):
        """A blank `datum` — the votering_id is missing from cases.parquet —
        makes `resolve_slug` answer None exactly as a budgetmotion does.

        Folding the two together hides a citation this pass serves among the
        ones it never touches, and `repair-citations` reads the same blank date,
        finds no document, and blanks the quote as the model's invention.
        `anchors.collect` draws the same line.
        """
        d = _decision(["vad som helst"], document="partiprogram")
        counts = mq.migrate_decision(d, "")
        assert (counts["unresolved"], counts["out_of_scope"]) == (1, 0)
        assert d["flags"] == []

    def test_a_valmanifest_citation_of_an_undated_vote_is_caught_too(self):
        """`resolve_slug` sees the gap only for a `partiprogram`.

        A `valmanifest` resolves from the party code with no date at all, so a
        votering_id missing from cases.parquet would migrate normally and the
        pass would exit 0 — the one report that is supposed to stop the pass
        order before `repair-citations` reads the same blank date. The test is
        on the date, not on the slug, for exactly that reason.
        """
        if mq.resolve_slug("valmanifest", "KD", "") is None:
            pytest.skip("corpus not extracted")
        d = _decision(["vad som helst"], document="valmanifest")
        counts = mq.migrate_decision(d, "")
        assert (counts["unresolved"], counts["out_of_scope"]) == (1, 0)
        assert d["flags"] == []
        assert d["citations"][0]["quote"] == "vad som helst"

    def test_a_second_run_changes_nothing(self, kd2015):
        original = _sentence(kd2015)
        d = _decision([_break_a_word(original)])
        mq.migrate_decision(d, "2023-04-12")
        first = json.dumps(d, ensure_ascii=False, sort_keys=True)
        mq.migrate_decision(d, "2023-04-12")
        assert json.dumps(d, ensure_ascii=False, sort_keys=True) == first

    def test_a_second_run_never_overwrites_the_sidecar(self, kd2015):
        """The sidecar is what the agent wrote. A re-run must not replace it
        with what the FIRST run wrote — that would erase the only copy."""
        original = _sentence(kd2015)
        damaged = _break_a_word(original)
        d = _decision([damaged])
        mq.migrate_decision(d, "2023-04-12")
        mq.migrate_decision(d, "2023-04-12")
        assert d["citations"][0]["quote_fore_migrering"] == damaged

    def test_a_stale_failure_flag_is_cleared_when_the_quote_now_places(self, kd2015):
        d = _decision([_sentence(kd2015)], flags=["citat_ej_migrerat", "citat_korrigerat"])
        mq.migrate_decision(d, "2023-04-12")
        assert d["flags"] == ["citat_korrigerat"]

    def test_flags_from_other_passes_survive(self, kd2015):
        d = _decision([_break_a_word(_sentence(kd2015))], flags=["citat_svagt"])
        mq.migrate_decision(d, "2023-04-12")
        assert d["flags"] == ["citat_svagt", "citat_migrerat"]

    def test_one_failure_among_several_citations_flags_the_decision(self, kd2015):
        d = _decision([_sentence(kd2015), "Ingenting av detta står i programmet någonstans."])
        counts = mq.migrate_decision(d, "2023-04-12")
        assert (counts["exact"], counts["failed"]) == (1, 1)
        assert d["flags"] == ["citat_ej_migrerat"]

    def test_the_locator_cache_is_shared_across_decisions(self, kd2015):
        cache = {}
        quote = _sentence(kd2015)
        for _ in range(3):
            mq.migrate_decision(_decision([quote]), "2023-04-12", cache)
        assert list(cache) == [("partiprogram-kd-2015", quote)]

    def test_failures_are_tallied_by_quote_for_the_report(self, kd2015):
        from collections import Counter

        junk = "Ingenting av detta står i programmet någonstans alls."
        failures = Counter()
        for _ in range(2):
            mq.migrate_decision(_decision([junk]), "2023-04-12", {}, failures)
        assert failures == Counter({("partiprogram-kd-2015", junk): 2})


class TestRun:
    """The write path over a throwaway copy of the run layout."""

    @pytest.fixture
    def sim(self, tmp_path, monkeypatch, kd2015):
        vid = _a_real_votering()
        d = tmp_path / "simulations" / "test-run"
        d.mkdir(parents=True)
        rows = [
            _decision([_sentence(kd2015)], vid=vid),
            _decision([_break_a_word(_sentence(kd2015))], vid=vid),
            _decision(["Ingenting av detta står i programmet någonstans alls."], vid=vid),
        ]
        rows[2]["prompt_version"] = "p5"  # frozen corpus — nothing to migrate
        (d / "KD.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )
        monkeypatch.setattr(mq, "RESULTS_DIR", tmp_path)
        return d / "KD.jsonl"

    def test_a_dry_run_writes_nothing(self, sim):
        before = sim.read_bytes()
        counts = mq.run("test-run", dry_run=True)
        assert sim.read_bytes() == before
        assert counts["fuzzy"] == 1

    def test_a_real_run_rewrites_only_the_quote_that_moved(self, sim, kd2015):
        mq.run("test-run")
        rows = [json.loads(line) for line in sim.read_text().splitlines()]
        assert len(rows) == 3
        assert rows[0]["flags"] == []
        assert rows[1]["flags"] == ["citat_migrerat"]
        assert rows[1]["citations"][0]["quote"] == _sentence(kd2015)
        assert "quote_fore_migrering" in rows[1]["citations"][0]

    def test_pre_p6_decisions_are_never_touched(self, sim):
        """They read `data/corpus/frozen/`, whose bytes this change did not move."""
        counts = mq.run("test-run")
        assert counts["pre_p6"] == 1
        rows = [json.loads(line) for line in sim.read_text().splitlines()]
        assert rows[2]["flags"] == []
        assert rows[2]["citations"][0]["quote"].startswith("Ingenting")

    def test_the_write_is_atomic_and_leaves_no_temp_file(self, sim):
        mq.run("test-run")
        assert list(sim.parent.glob("*.tmp")) == []
        assert sim.read_text().endswith("\n")

    def test_running_twice_is_byte_identical(self, sim):
        mq.run("test-run")
        once = sim.read_bytes()
        mq.run("test-run")
        assert sim.read_bytes() == once

    def test_a_run_that_does_not_exist_is_refused(self, sim):
        """A typoed `--run-id` globs nothing and reports what a no-op reports.

        Exiting 0 on it reads as "the corpus move touched no quote", and the next
        pass then books every migrated quote as a model paraphrase — the
        misattribution the migrate/repair order exists to prevent.
        """
        with pytest.raises(FileNotFoundError, match="nothing to migrate"):
            mq.run("test-runn")

    def test_an_empty_run_directory_is_refused_too(self, sim, tmp_path):
        (tmp_path / "simulations" / "empty-run").mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="nothing to migrate"):
            mq.run("empty-run")


class TestTheCommandFailsOnAnUnresolvedCitation:
    """`unresolved` is a defect, and nothing downstream of it ever raises.

    A citation naming a class this pass serves that resolves to no edition is an
    ingest gap: `datum` came back "" because the `votering_id` is not in
    cases.parquet. `repair-citations` reads that same blank date, finds no
    document to verify against and blanks the quote as `citat_ej_verifierad` —
    booking a missing case as the model inventing a quote. `build-anchors`' own
    guard cannot catch it either: by then the quote is blank, and
    `anchors.collect` excuses blanks before it resolves a slug. So the exit code
    here is the only place the pass order can stop.
    """

    def _invoke(self, tmp_path, monkeypatch, rows, extra=()):
        from typer.testing import CliRunner

        from aidag import cli

        # These two are the only tests in this file that reach `program_at`,
        # which dates a citation by looking its vote up in cases.parquet. That
        # file is gitignored — the data is re-fetchable from public APIs — so CI
        # arrives here with no data tree and the CliRunner swallows the
        # FileNotFoundError into a non-zero exit that reads as the assertion
        # failing. Skip, the way test_agent_pipeline.py does for the same file.
        if not (PROCESSED_DIR / "cases.parquet").exists():
            pytest.skip("cases.parquet not built (run: uv run aidag build-cases)")

        d = tmp_path / "simulations" / "test-run"
        d.mkdir(parents=True)
        (d / "KD.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )
        monkeypatch.setattr(mq, "RESULTS_DIR", tmp_path)
        return CliRunner().invoke(
            cli.app, ["migrate-quotes", "--run-id", "test-run", *extra]
        )

    def _undatable(self, kd2015):
        # vid absent from cases.parquet -> datum "" -> `program_at` names no edition
        return _decision([_sentence(kd2015)], vid="INGEN-SADAN-VOTERING")

    def test_an_undatable_citation_exits_non_zero(self, tmp_path, monkeypatch, kd2015):
        res = self._invoke(tmp_path, monkeypatch, [self._undatable(kd2015)])
        assert res.exit_code == 1
        assert "1 unresolved" in res.output
        # the report still prints — the exit code is added to it, not instead of it
        assert "re-run the ingest" in res.output.lower()

    def test_a_resolvable_run_exits_zero(self, tmp_path, monkeypatch, kd2015):
        rows = [_decision([_sentence(kd2015)], vid=_a_real_votering())]
        res = self._invoke(tmp_path, monkeypatch, rows)
        assert res.exit_code == 0, res.output

    def test_a_dry_run_fails_on_it_too(self, tmp_path, monkeypatch, kd2015):
        """The defect is in the record, not in the write — reporting it and
        exiting 0 is what lets the pass order walk past it."""
        res = self._invoke(
            tmp_path, monkeypatch, [self._undatable(kd2015)], extra=["--dry-run"]
        )
        assert res.exit_code == 1

    def test_an_out_of_scope_document_is_not_a_failure(self, tmp_path, monkeypatch):
        """budgetmotioner keep the flat extraction; resolving to no block file is
        what they are supposed to do."""
        rows = [_decision(["Vad som helst."], document="budgetmotion", vid="TEST-VID")]
        res = self._invoke(tmp_path, monkeypatch, rows)
        assert res.exit_code == 0, res.output
        assert "1 out of scope" in res.output
