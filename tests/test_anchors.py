"""The reverse index — a corpus line, and the votes that leaned on it.

Fixtures are real corpus documents and real block ids. The thing that makes this
index hard to get right is not the grouping, it is the pairing: the served text
is one line per surviving block, and if that correspondence slips by one every
anchor after it names the wrong line while every count stays plausible. So the
pairing is tested against the real files, and the failure is tested by breaking
it deliberately.

The build gate gets the same treatment. `build-anchors` is allowed to refuse a
run, and a gate that has never been seen to fire is not a gate.
"""

import json
import re

import pytest

from aidag import anchors
from aidag.config import CORPUS_DIR
from aidag.simulate import _normalize_ws

KD2015 = "partiprogram-kd-2015"
KD2025 = "partiprogram-kd-2025"


def _index(slug: str) -> anchors.BlockIndex:
    if not (CORPUS_DIR / f"{slug}.txt").exists():
        pytest.skip(f"{slug} not extracted (run: uv run aidag extract-corpus)")
    return anchors.block_index(slug)


@pytest.fixture(scope="module")
def kd2015():
    return _index(KD2015)


def _a_block(idx: anchors.BlockIndex, min_words: int = 14) -> tuple[str, str]:
    """(block_id, its served text) — long enough to locate unambiguously."""
    for i, bid in enumerate(idx.ids):
        text = idx.served[idx.starts[i] : idx.ends[i]]
        if len(text.split()) >= min_words and text[0].isupper():
            return bid, text
    raise AssertionError("no usable block")


def _decision(
    quotes,
    parti="KD",
    document="partiprogram",
    rost="Ja",
    vid="V1",
    flags=None,
    arm="anonymous",
    prompt_version="p6",
    svag=False,
):
    return {
        "votering_id": vid,
        "parti": parti,
        "prompt_version": prompt_version,
        "arm": arm,
        "rost": rost,
        "hallning": "avvisar" if rost == "Ja" else "stodjer",
        "coverage": "explicit",
        "confidence": "high",
        "plan_tacker_utskottets_skal": "ja",
        "flags": list(flags or []),
        "citations": [
            {"document": document, "quote": q, "princip": "p", **({"svag": True} if svag else {})}
            for q in quotes
        ],
    }


CASES = {
    "V1": {"votering_id": "V1", "datum": "2023-04-12", "utskott": "SfU"},
    "V2": {"votering_id": "V2", "datum": "2023-05-10", "utskott": "SfU"},
}
POSITIONS = {("V1", "KD"): "Ja", ("V2", "KD"): "Ja"}


class TestVerdict:
    """`rost` is derived from `hallning`, so this is the GAP, not vote accuracy."""

    def test_the_plan_and_the_floor_agree(self):
        assert anchors.verdict_of("Ja", "Ja") == "kept"
        assert anchors.verdict_of("Nej", "Nej") == "kept"

    def test_the_plan_and_the_floor_disagree(self):
        assert anchors.verdict_of("Ja", "Nej") == "diverged"
        assert anchors.verdict_of("Nej", "Ja") == "diverged"

    def test_an_abstention_is_neither(self):
        """gap.py scores only ('Ja','Nej') — the plan was never asked to predict
        a floor tactic, so an abstention may not be reported as a divergence."""
        for rost in ("Ja", "Nej"):
            assert anchors.verdict_of(rost, "Avstår") == "avstar"

    def test_absence_is_its_own_verdict_however_it_arrives(self):
        """A party with no `party_positions` row cast no votes in the division,
        which is the same fact `build_cases` writes as 'Frånvarande'."""
        assert anchors.verdict_of("Ja", "Frånvarande") == "franvarande"
        assert anchors.verdict_of("Ja", None) == "franvarande"

    def test_every_verdict_is_declared(self):
        seen = {anchors.verdict_of("Ja", p) for p in ("Ja", "Nej", "Avstår", None)}
        assert seen == set(anchors.VERDICTS)

    def test_no_committed_position_falls_through_to_an_unintended_branch(self):
        """The real table, not a hand-written list of what it might contain."""
        import polars as pl

        from aidag.config import PROCESSED_DIR

        path = PROCESSED_DIR / "party_positions.parquet"
        if not path.exists():
            pytest.skip("party_positions not built")
        seen = set(pl.read_parquet(path, columns=["position"])["position"].unique())
        assert seen <= {"Ja", "Nej", "Avstår", "Frånvarande"}
        for position in seen:
            assert anchors.verdict_of("Ja", position) in anchors.VERDICTS


class TestBlockIndex:
    def test_a_quote_resolves_to_the_block_that_holds_it(self, kd2015):
        bid, text = _a_block(kd2015)
        assert kd2015.locate(text) == (bid, 0)

    def test_the_offset_is_measured_inside_the_block(self, kd2015):
        bid, text = _a_block(kd2015)
        tail = " ".join(text.split()[4:])
        assert kd2015.locate(tail) == (bid, len(text) - len(tail))

    def test_whitespace_is_collapsed_first_so_verify_and_this_agree(self, kd2015):
        bid, text = _a_block(kd2015)
        assert kd2015.locate("  \n  ".join(text.split())) == (bid, 0)

    def test_a_quote_that_is_not_in_the_document_does_not_resolve(self, kd2015):
        assert kd2015.locate("Riksdagen bör ersättas med ett lotteri om skattemedlen.") is None

    def test_a_blank_quote_does_not_resolve(self, kd2015):
        assert kd2015.locate("") is None
        assert kd2015.locate("   ") is None

    def test_offset_and_length_cut_the_quote_back_out_of_the_block(self, kd2015):
        """What Task 8's renderer needs: the span is a slice of the block, so a
        cited line can be marked without re-running the matcher in the browser."""
        _, text = _a_block(kd2015)
        tail = " ".join(text.split()[3:])
        bid, offset = kd2015.locate(tail)
        block = kd2015.served[
            kd2015.starts[kd2015.ids.index(bid)] : kd2015.ends[kd2015.ids.index(bid)]
        ]
        assert block[offset : offset + len(tail)] == tail

    def test_the_served_text_is_the_one_verify_compares_against(self, kd2015):
        from aidag.migrate_quotes import index_for

        assert kd2015.served == index_for(KD2015).served

    def test_every_block_id_comes_from_the_committed_block_file(self, kd2015):
        from aidag.extract_corpus import blocks_path

        ids = {b["id"] for b in json.loads(blocks_path(KD2015).read_text())["blocks"]}
        assert set(kd2015.ids) <= ids
        assert len(set(kd2015.ids)) == len(kd2015.ids)

    def test_the_pairing_holds_for_every_extracted_document(self):
        """The invariant the whole index rests on: served line i IS block i."""
        from aidag.extract_corpus import cited_slugs

        for slug in cited_slugs():
            if not (CORPUS_DIR / f"{slug}.txt").exists():
                pytest.skip(f"{slug} not extracted")
            idx = anchors.block_index(slug)  # raises if the pairing has drifted
            assert len(idx.ids) == len(idx.starts) == len(idx.ends)

    def test_a_drifted_block_file_raises_instead_of_shifting_every_anchor(
        self, kd2015, tmp_path, monkeypatch
    ):
        """Off-by-one here is invisible in the output — it renames every line."""
        from aidag.extract_corpus import blocks_path

        payload = json.loads(blocks_path(KD2015).read_text())
        payload["blocks"].insert(0, {"id": "bXXXX", "role": "para", "page": 0,
                                     "size": 11.0, "bold": False, "text": "Inskjuten rad."})
        bogus = tmp_path / f"{KD2015}.json"
        bogus.write_text(json.dumps(payload, ensure_ascii=False))
        monkeypatch.setattr("aidag.extract_corpus.blocks_path", lambda slug: bogus)
        anchors.block_index.cache_clear()
        with pytest.raises(ValueError, match="lines — blocks/"):
            anchors.block_index(KD2015)
        anchors.block_index.cache_clear()

    def test_a_block_that_kept_its_place_but_changed_its_text_raises(
        self, kd2015, tmp_path, monkeypatch
    ):
        """The count check cannot see this one, and it is the likelier drift.

        Inserting a block trips the length comparison before the pairing loop
        ever runs. An *edited* block leaves the count intact, so only the
        per-block round-trip catches it — and without that the served line and
        the block claiming to be it silently disagree.
        """
        from aidag.extract_corpus import blocks_path

        payload = json.loads(blocks_path(KD2015).read_text())
        edited = next(b for b in payload["blocks"] if len(b["text"]) > 40)
        edited["text"] = "Utbytt text som inte står i dokumentet."
        bogus = tmp_path / f"{KD2015}.json"
        bogus.write_text(json.dumps(payload, ensure_ascii=False))
        monkeypatch.setattr("aidag.extract_corpus.blocks_path", lambda slug: bogus)
        anchors.block_index.cache_clear()
        with pytest.raises(ValueError, match="does not match the served line"):
            anchors.block_index(KD2015)
        anchors.block_index.cache_clear()

    def test_a_missing_block_file_names_the_command_that_writes_it(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "aidag.extract_corpus.blocks_path", lambda slug: tmp_path / "absent.json"
        )
        anchors.block_index.cache_clear()
        with pytest.raises(FileNotFoundError, match="extract-corpus"):
            anchors.block_index(KD2015)
        anchors.block_index.cache_clear()


class TestCollect:
    def test_a_citation_becomes_one_ref_under_its_block(self, kd2015):
        bid, text = _a_block(kd2015)
        by_slug, counts, unlocated, _ = anchors.collect(
            [_decision([text])], CASES, POSITIONS
        )
        assert unlocated == []
        assert list(by_slug) == [KD2015]
        (anchor,) = by_slug[KD2015]
        assert (anchor.block_id, anchor.offset) == (bid, 0)
        assert anchor.refs == [{
            "votering_id": "V1", "parti": "KD", "verdict": "kept",
            "tier": "explicit", "utskott": "SfU", "datum": "2023-04-12", "svag": False,
        }]
        assert counts["located"] == 1

    def test_two_decisions_citing_one_line_share_an_anchor(self, kd2015):
        _, text = _a_block(kd2015)
        rows = [_decision([text]), _decision([text], parti="KD", vid="V1", rost="Nej")]
        by_slug, counts, _, _ = anchors.collect(rows, CASES, POSITIONS)
        (anchor,) = by_slug[KD2015]
        assert len(anchor.refs) == 2
        assert {r["verdict"] for r in anchor.refs} == {"kept", "diverged"}
        assert counts["anchors"] == 1

    def test_one_decision_citing_the_same_line_twice_is_one_vote(self, kd2015):
        _, text = _a_block(kd2015)
        by_slug, counts, _, _ = anchors.collect([_decision([text, text])], CASES, POSITIONS)
        (anchor,) = by_slug[KD2015]
        assert len(anchor.refs) == 1
        assert counts["duplicate_ref"] == 1

    def test_the_weak_marker_rides_along(self, kd2015):
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text], svag=True)], CASES, POSITIONS)
        assert by_slug[KD2015][0].refs[0]["svag"] is True

    def test_a_blank_quote_is_skipped_and_does_not_fail_the_build(self):
        by_slug, counts, unlocated, _ = anchors.collect([_decision([""])], CASES, POSITIONS)
        assert (by_slug, unlocated) == ({}, [])
        assert counts["blank"] == 1

    def test_an_unlocatable_quote_is_reported(self, kd2015):
        junk = "Detta har aldrig stått i något partiprogram över huvud taget."
        _, counts, unlocated, _ = anchors.collect([_decision([junk])], CASES, POSITIONS)
        assert counts["unlocated"] == 1
        assert unlocated == [
            {"document": KD2015, "quote": junk, "cid": "V1", "parti": "KD"}
        ]

    def test_an_unlocatable_quote_the_record_already_flagged_is_excused(self, kd2015):
        junk = "Detta har aldrig stått i något partiprogram över huvud taget."
        _, counts, unlocated, _ = anchors.collect(
            [_decision([junk], flags=["citat_ej_migrerat"])], CASES, POSITIONS
        )
        assert unlocated == []
        assert counts["unlocated_excused"] == 1

    def test_a_pre_p6_decision_is_skipped(self, kd2015):
        """Those runs read data/corpus/frozen/; these blocks are not their text."""
        _, text = _a_block(kd2015)
        by_slug, counts, _, _ = anchors.collect(
            [_decision([text], prompt_version="p5")], CASES, POSITIONS
        )
        assert (by_slug, counts["pre_p6"]) == ({}, 1)

    def test_a_non_anonymous_arm_is_skipped(self, kd2015):
        _, text = _a_block(kd2015)
        by_slug, counts, _, _ = anchors.collect(
            [_decision([text], arm="named")], CASES, POSITIONS
        )
        assert (by_slug, counts["other_arm"]) == ({}, 1)

    def test_a_document_class_without_blocks_is_out_of_scope(self):
        _, counts, unlocated, unresolved = anchors.collect(
            [_decision(["vad som helst"], document="budgetmotion")], CASES, POSITIONS
        )
        assert (counts["out_of_scope"], unlocated, unresolved) == (1, [], [])

    def test_an_in_scope_class_that_resolves_to_nothing_is_not_counted_away(self):
        """`out_of_scope` used to absorb this too, and it is a different thing.

        A votering_id missing from cases.parquet gives `datum=""`, no programme
        edition can be dated from it, and `resolve_slug` answers None exactly as
        it does for a budgetmotion. Dropping the citation there is the silent
        link-rot this module exists to stop, so it is collected for the caller
        to raise on.
        """
        _, counts, unlocated, unresolved = anchors.collect(
            [_decision(["vad som helst"], document="partiprogram")], {}, POSITIONS
        )
        assert counts["out_of_scope"] == 0
        assert unlocated == []
        assert [(u["document"], u["parti"], u["datum"]) for u in unresolved] == [
            ("partiprogram", "KD", "")
        ]

    def test_anchors_come_out_in_reading_order(self, kd2015):
        blocks = [
            (i, kd2015.served[kd2015.starts[i] : kd2015.ends[i]])
            for i in range(len(kd2015.ids))
        ]
        picks = [t for _, t in blocks if len(t.split()) >= 14][:4]
        rows = [_decision([q]) for q in reversed(picks)]
        by_slug, _, _, _ = anchors.collect(rows, CASES, POSITIONS)
        order = {bid: i for i, bid in enumerate(kd2015.ids)}
        got = [(order[a.block_id], a.offset) for a in by_slug[KD2015]]
        assert got == sorted(got)


class TestDateGatedResolution:
    """A citation names a CLASS. Which edition it meant is a date."""

    def test_a_2023_vote_anchors_into_the_2015_programme(self):
        idx = _index(KD2015)
        _, text = _a_block(idx)
        cases = {"V1": {"datum": "2023-04-12", "utskott": "SfU"}}
        by_slug, _, unlocated, _ = anchors.collect([_decision([text])], cases, POSITIONS)
        assert list(by_slug) == [KD2015]
        assert unlocated == []

    def test_a_2026_vote_anchors_into_the_2025_programme(self):
        idx = _index(KD2025)
        _, text = _a_block(idx)
        cases = {"V1": {"datum": "2026-01-15", "utskott": "SfU"}}
        by_slug, _, _, _ = anchors.collect([_decision([text])], cases, POSITIONS)
        assert list(by_slug) == [KD2025]

    def test_the_older_edition_is_not_consulted_for_a_later_vote(self):
        """Every citation of a 2026 vote is offered to the 2025 edition alone —
        whether the quote then resolves there is a fact about the two documents,
        not something the resolver may fall back on."""
        cases = {"V1": {"datum": "2026-01-15", "utskott": "SfU"}}
        _, text = _a_block(_index(KD2015))
        by_slug, _, unlocated, _ = anchors.collect([_decision([text])], cases, POSITIONS)
        assert KD2015 not in by_slug
        assert all(u["document"] == KD2025 for u in unlocated)
        assert set(by_slug) | {u["document"] for u in unlocated} == {KD2025}


class TestBuild:
    """The write path and the gate, over a throwaway run layout."""

    @pytest.fixture
    def run_root(self, tmp_path, monkeypatch, kd2015):
        monkeypatch.setattr(anchors, "_case_tables", lambda: (CASES, POSITIONS))
        d = tmp_path / "simulations" / "test-run"
        d.mkdir(parents=True)
        return tmp_path, d

    def _write(self, sim_dir, rows):
        (sim_dir / "KD.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )

    def test_it_writes_one_file_per_cited_document(self, run_root, kd2015):
        root, sim = run_root
        bid, text = _a_block(kd2015)
        self._write(sim, [_decision([text])])
        anchors.build("test-run", results_dir=root)
        out = anchors.anchors_dir("test-run", root) / f"{KD2015}.json"
        payload = json.loads(out.read_text())
        assert payload["slug"] == KD2015
        assert (payload["n_anchors"], payload["n_refs"]) == (1, 1)
        assert payload["anchors"][0]["block_id"] == bid
        assert payload["anchors"][0]["length"] == len(_normalize_ws(text))

    def test_an_unlocatable_quote_fails_the_build(self, run_root, kd2015):
        root, sim = run_root
        self._write(sim, [_decision(["Detta står ingenstans i något program alls."])])
        with pytest.raises(anchors.Unlocated, match="citat_ej_migrerat"):
            anchors.build("test-run", results_dir=root)
        assert not anchors.anchors_dir("test-run", root).exists()

    def test_a_blanked_quote_does_not_fail_the_build(self, run_root, kd2015):
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text, ""])])
        result = anchors.build("test-run", results_dir=root)
        assert result["counts"]["blank"] == 1
        assert result["report"] == [{"slug": KD2015, "anchors": 1, "refs": 1}]

    def test_a_flagged_quote_does_not_fail_the_build(self, run_root, kd2015):
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text, "Detta står ingenstans alls."],
                                    flags=["citat_ej_migrerat"])])
        result = anchors.build("test-run", results_dir=root)
        assert result["counts"]["unlocated_excused"] == 1

    def test_the_refusal_names_the_quotes(self, run_root, kd2015):
        root, sim = run_root
        self._write(sim, [_decision(["Detta står ingenstans i något program alls."])])
        with pytest.raises(anchors.Unlocated, match="Detta står ingenstans"):
            anchors.build("test-run", results_dir=root)

    def test_running_twice_is_byte_identical(self, run_root, kd2015):
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text])])
        anchors.build("test-run", results_dir=root)
        out = anchors.anchors_dir("test-run", root) / f"{KD2015}.json"
        once = out.read_bytes()
        anchors.build("test-run", results_dir=root)
        assert out.read_bytes() == once

    def test_a_slug_that_stops_being_cited_loses_its_file(self, run_root, kd2015):
        """`load()` globs this directory, so a survivor is re-exported forever."""
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text])])
        anchors.build("test-run", results_dir=root)
        stale = anchors.anchors_dir("test-run", root) / "partiprogram-x-1999.json"
        stale.write_text('{"slug": "partiprogram-x-1999"}')
        anchors.build("test-run", results_dir=root)
        assert not stale.exists()
        assert set(anchors.load("test-run", root)) == {KD2015}

    def test_a_refused_build_leaves_the_standing_index_alone(self, run_root, kd2015):
        """The prune happens after collect(), so an Unlocated raise costs nothing."""
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text])])
        anchors.build("test-run", results_dir=root)
        out = anchors.anchors_dir("test-run", root) / f"{KD2015}.json"
        before = out.read_bytes()
        self._write(sim, [_decision(["Detta står ingenstans i något program alls."])])
        with pytest.raises(anchors.Unlocated):
            anchors.build("test-run", results_dir=root)
        assert out.read_bytes() == before

    def test_a_run_with_no_shards_refuses_rather_than_pruning_everything(
        self, run_root, kd2015
    ):
        """`glob` cannot tell a missing run from an empty one, and an empty one
        reaches write() with nothing unlocated — which unlinks the whole index
        and reports `0 decisions` as if that were the answer."""
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text])])
        anchors.build("test-run", results_dir=root)
        out = anchors.anchors_dir("test-run", root) / f"{KD2015}.json"
        before = out.read_bytes()
        (sim / "KD.jsonl").unlink()
        with pytest.raises(FileNotFoundError, match="nothing to index"):
            anchors.build("test-run", results_dir=root)
        assert out.read_bytes() == before

    def test_a_run_that_indexes_nothing_refuses_to_write_an_empty_index(
        self, run_root, kd2015
    ):
        """`build-anchors` against a pre-p6 run — full-v3 is one, and the
        documented pass order still runs it.

        The skip happens inside collect(), so `_load_run`'s "refuse rather than
        rebuild to nothing" check passes on the way in and by_slug is {} on the
        way out. write() would then mkdir an index directory holding no files,
        and `export_blocks_and_anchors` prunes the committed anchors down to
        whatever that directory says — all 46 files, with a success line.
        """
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text], prompt_version="p5")])
        with pytest.raises(anchors.EmptyIndex, match="pre-p6"):
            anchors.build("test-run", results_dir=root)
        assert not anchors.anchors_dir("test-run", root).exists()

    def test_a_refusal_to_index_nothing_leaves_a_standing_index_alone(
        self, run_root, kd2015
    ):
        root, sim = run_root
        _, text = _a_block(kd2015)
        self._write(sim, [_decision([text])])
        anchors.build("test-run", results_dir=root)
        out = anchors.anchors_dir("test-run", root) / f"{KD2015}.json"
        before = out.read_bytes()
        self._write(sim, [_decision([text], prompt_version="p5")])
        with pytest.raises(anchors.EmptyIndex):
            anchors.build("test-run", results_dir=root)
        assert out.read_bytes() == before

    def test_a_missing_run_directory_refuses_too(self, run_root, kd2015):
        root, _ = run_root
        with pytest.raises(FileNotFoundError, match="nothing to index"):
            anchors.build("no-such-run", results_dir=root)

    def test_an_in_scope_citation_that_resolves_to_nothing_fails_the_build(
        self, run_root, kd2015, monkeypatch
    ):
        """A votering_id missing from cases.parquet dates no edition, so the
        citation would drop out of the index with only a counter moving."""
        root, sim = run_root
        monkeypatch.setattr(anchors, "_case_tables", lambda: ({}, POSITIONS))
        self._write(sim, [_decision(["vad som helst"], document="partiprogram")])
        with pytest.raises(anchors.Unlocated, match="resolve to no document"):
            anchors.build("test-run", results_dir=root)


class TestSiteShapes:
    """Page weight: aggregates inline, ref lists fetched."""

    @pytest.fixture
    def payload(self, kd2015):
        _, text = _a_block(kd2015)
        rows = [_decision([text]), _decision([text], vid="V2", rost="Nej")]
        by_slug, _, _, _ = anchors.collect(rows, CASES, POSITIONS)
        anchor = by_slug[KD2015][0]
        return {
            "slug": KD2015,
            "run_id": "test-run",
            "n_anchors": 1,
            "n_refs": len(anchor.refs),
            "anchors": [anchor.to_dict()],
        }

    @pytest.fixture
    def twice_cited(self, kd2015):
        """One decision quoting two spans of the same block — 1,679 of full-v4's
        4,715 cited blocks carry at least one such pair."""
        bid, text = _a_block(kd2015, min_words=30)
        words = text.split()
        head, tail = " ".join(words[:14]), " ".join(words[-14:])
        by_slug, _, _, _ = anchors.collect([_decision([head, tail])], CASES, POSITIONS)
        found = by_slug[KD2015]
        assert [a.block_id for a in found] == [bid, bid]
        return {
            "slug": KD2015,
            "run_id": "test-run",
            "n_anchors": len(found),
            "n_refs": sum(len(a.refs) for a in found),
            "anchors": [a.to_dict() for a in found],
        }

    def test_the_inline_summary_counts_by_block(self, payload):
        s = anchors.summarize(payload)
        block = s["blocks"][payload["anchors"][0]["block_id"]]
        assert (block["refs"], block["kept"], block["diverged"]) == (2, 1, 1)
        assert block["decisions"] == {"n": 2, "kept": 1, "diverged": 1,
                                      "avstar": 0, "franvarande": 0}
        assert block["tiers"] == {"explicit": 2}
        assert block["parties"] == {"KD": 2}
        assert s["totals"] == {"anchors": 1, "refs": 2, "kept": 1, "diverged": 1,
                               "avstar": 0, "franvarande": 0,
                               "decisions": {"n": 2, "kept": 1, "diverged": 1,
                                             "avstar": 0, "franvarande": 0}}

    def test_a_vote_quoting_one_line_twice_is_one_vote(self, twice_cited):
        """The page says "N votes leaned on this line". Two citations from the
        same decision are one vote that read the line, and the ref list behind
        the bar shows it once — so the headline count has to agree with it."""
        s = anchors.summarize(twice_cited)
        (block,) = s["blocks"].values()
        assert block["refs"] == 2
        assert block["decisions"] == {"n": 1, "kept": 1, "diverged": 0,
                                      "avstar": 0, "franvarande": 0}
        assert s["totals"]["decisions"]["n"] == 1

    def test_a_tier_histogram_is_not_weighted_by_how_often_a_vote_quoted(self, twice_cited):
        """`tier` is a property of the decision, not of the quote."""
        s = anchors.summarize(twice_cited)
        (block,) = s["blocks"].values()
        assert block["tiers"] == {"explicit": 1}
        assert block["parties"] == {"KD": 1}

    def test_a_vote_citing_two_different_lines_is_one_vote_in_the_document_total(self, kd2015):
        """Distinct per block AND distinct across the document: the rail says how
        many votes read this document, not how many lines they read."""
        first, first_text = _a_block(kd2015, min_words=20)
        second = next(
            (bid, kd2015.served[kd2015.starts[i] : kd2015.ends[i]])
            for i, bid in enumerate(kd2015.ids)
            if bid != first and len(kd2015.served[kd2015.starts[i] : kd2015.ends[i]].split()) >= 20
            and kd2015.served[kd2015.starts[i] : kd2015.ends[i]][0].isupper()
        )
        by_slug, _, _, _ = anchors.collect([_decision([first_text, second[1]])], CASES, POSITIONS)
        found = by_slug[KD2015]
        s = anchors.summarize({
            "slug": KD2015, "run_id": "test-run", "n_anchors": len(found),
            "n_refs": sum(len(a.refs) for a in found),
            "anchors": [a.to_dict() for a in found],
        })
        assert len(s["blocks"]) == 2
        assert all(b["decisions"]["n"] == 1 for b in s["blocks"].values())
        assert s["totals"]["refs"] == 2
        assert s["totals"]["decisions"]["n"] == 1

    def test_the_inline_summary_carries_no_ref_rows_and_no_quote_text(self, payload):
        """9,007 refs on one document is why this half exists at all."""
        blob = json.dumps(anchors.summarize(payload), ensure_ascii=False)
        assert "votering_id" not in blob
        assert payload["anchors"][0]["quote"][:40] not in blob

    def test_the_inline_summary_keeps_the_span_so_the_line_can_be_marked(self, payload):
        s = anchors.summarize(payload)
        (block,) = s["blocks"].values()
        assert block["anchors"] == [
            {"offset": 0, "length": payload["anchors"][0]["length"],
             "refs": 2, "kept": 1, "diverged": 1, "avstar": 0, "franvarande": 0}
        ]

    def test_the_fetched_half_is_rows_keyed_by_a_declared_field_order(self, payload):
        c = anchors.compact_refs(payload)
        assert c["fields"] == list(anchors.REF_FIELDS)
        row = c["anchors"][0]["refs"][0]
        assert dict(zip(c["fields"], row)) == payload["anchors"][0]["refs"][0]

    def test_the_fetched_half_names_the_block_it_belongs_to(self, payload):
        c = anchors.compact_refs(payload)
        assert c["anchors"][0]["block_id"] == payload["anchors"][0]["block_id"]
        assert len(c["anchors"][0]["refs"]) == payload["n_refs"]


KDVAL = "valmanifest-2022-kd"
MVAL = "valmanifest-2022-m"


class TestNavigationRule:
    """Mirrors `site/src/lib/anchors.ts:isNavigational`, case for case.

    The rule is written twice — once per language — so these are the assertions
    `site/tests/anchors.test.mjs` makes, repeated here. A change on one side that
    is not made on the other fails in exactly one suite, which is the point.
    """

    def test_a_topic_label_is_navigation(self):
        assert anchors.is_navigational("label", "EN STRAM MIGRATION")
        assert anchors.is_navigational("h3", "FLER JOBB FLER FÖRETAG")
        assert anchors.is_navigational("h1", "KAPITEL 1. Kristdemokratins värdegrund")

    def test_a_contents_entry_is_navigation_whatever_it_says(self):
        assert anchors.is_navigational(
            "toc", "Kampen mot kriminaliteten ska vinnas i varje del av landet."
        )

    def test_a_pledge_set_as_a_bold_label_is_not(self):
        """The false claim this rule exists to avoid, 1,163 decisions' worth."""
        assert not anchors.is_navigational(
            "label", "• Kraftigt öka antalet poliser på våra gator och torg."
        )
        assert not anchors.is_navigational(
            "label",
            "13. En högre utbildning med högre ambitioner. Vi vill öka den "
            "lärarledda undervisningen.",
        )

    def test_body_text_is_never_navigation_however_it_looks(self):
        for role in ("para", "bullet", "caption", "unreadable"):
            assert not anchors.is_navigational(role, "EN STRAM MIGRATION"), role

    def test_a_heading_that_states_something_is_not_reduced_to_a_label(self):
        assert not anchors.is_navigational("h2", "Vi ska avskaffa fastighetsskatten.")
        assert not anchors.is_navigational(
            "h2", "Ett samhälle där varje människa räknas och där ingen lämnas efter av staten"
        )

    def test_a_pledge_heading_survives_dropping_its_full_stop(self):
        """The heading-side half of the false claim above.

        A heading omits the terminal period by typographic convention, so the
        fixture in the test before this one passes on punctuation the real
        documents do not set. These are `valmanifest-2022-m`'s and `-s`'s
        headline pledges verbatim — 10 blocks, 96 of the 180 (vote, line) pairs
        the flag reached while the text test was punctuation alone.
        """
        for t in (
            "Statens utgifter ska minska",
            "Vi ska stötta, inte styra, jord- och skogsbruket",
            "Vi ska stoppa mäns våld mot kvinnor",
            "Du ska ha råd med elräkningen",
            "Vi ska stå upp för hbtq-personers rättigheter",
            "Bryt segregationen för att hålla ihop Sverige",  # imperative
        ):
            assert not anchors.is_navigational("h2", t), t

    def test_the_topic_labels_the_rule_is_for_still_pass_it(self):
        """The other side of the same cut — these carry no verb at all."""
        for t in (
            "STÄRKT CIVILSAMHÄLLE FÖRBÄTTRAD INTEGRATION FLER BOSTÄDER",
            "JÄMSTÄLLDHET PÅ RIKTIGT",
            "STARKARE FAMILJER",
            "Den politiska demokratin – ofullgången men ovärderlig",
        ):
            assert anchors.is_navigational("h3", t), t

    def test_the_length_cut_is_where_it_says_it_is(self):
        """Literal 8/9, not NAV_MAX_WORDS — deriving the boundary from the
        constant under test moves both sides of the assertion together, so the
        cut would pass wherever it was moved to."""
        assert anchors.NAV_MAX_WORDS == 8
        assert anchors.is_navigational("h2", "ord ord ord ord ord ord ord ord")
        assert not anchors.is_navigational("h2", "ord ord ord ord ord ord ord ord ord")

    def test_a_quoted_full_stop_still_ends_a_sentence(self):
        assert not anchors.is_navigational("h2", 'Han sade "vi ska vinna."')


class TestCitedBlocks:
    """The report's input: every cited block, with the role it carries."""

    @pytest.fixture
    def run(self, tmp_path, monkeypatch, kd2015):
        monkeypatch.setattr(anchors, "_case_tables", lambda: (CASES, POSITIONS))
        sim = tmp_path / "simulations" / "test-run"
        sim.mkdir(parents=True)
        return tmp_path, sim

    def _build(self, root, sim, rows):
        (sim / "d.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )
        anchors.build("test-run", results_dir=root)

    def test_a_cited_block_carries_its_role_and_its_text(self, run, kd2015):
        root, sim = run
        bid, text = _a_block(kd2015)
        self._build(root, sim, [_decision([text])])
        (row,) = anchors.cited_blocks("test-run", root)
        assert (row["slug"], row["block_id"]) == (KD2015, bid)
        assert row["role"] == anchors.block_roles(KD2015)[bid]["role"]
        assert row["citations"] == 1
        assert row["votes"] == {("V1", "KD")}

    def test_two_spans_of_one_line_are_one_cited_block(self, run, kd2015):
        """Per block, not per anchor — the question is about the line."""
        _, text = _a_block(kd2015, min_words=30)
        words = text.split()
        self._build(root_sim := run[0], run[1],
                    [_decision([" ".join(words[:14]), " ".join(words[-14:])])])
        (row,) = anchors.cited_blocks("test-run", root_sim)
        assert (row["citations"], row["votes"]) == (2, {("V1", "KD")})

    def test_a_cited_block_missing_from_blocks_raises(self, run, kd2015, monkeypatch):
        """Anchors were built from those blocks — a gap means they have drifted,
        and every role in the report would be a guess."""
        root, sim = run
        _, text = _a_block(kd2015)
        self._build(root, sim, [_decision([text])])
        monkeypatch.setattr(anchors, "block_roles", lambda slug: {})
        with pytest.raises(ValueError, match="build-anchors"):
            anchors.cited_blocks("test-run", root)


def _row(slug, block_id, role, text, votes, citations=None, page=0):
    return {
        "slug": slug,
        "block_id": block_id,
        "role": role,
        "text": text,
        "page": page,
        "citations": citations if citations is not None else len(votes),
        "votes": set(votes),
        "navigational": anchors.is_navigational(role, text),
    }


class TestNavigationReport:
    """The aggregation — three scopes, and the difference between them."""

    @pytest.fixture
    def rows(self):
        return [
            _row(KDVAL, "b0094", "h3", "FLER JOBB FLER FÖRETAG", [("V1", "KD"), ("V2", "KD")]),
            _row(KDVAL, "b0095", "h3", "STARKARE FAMILJER", [("V2", "KD")]),
            _row("valmanifest-2022-s", "b0041", "label",
                 "• Krafttag för att stoppa hedersrelaterat våld.", [("V3", "S")]),
            _row(KDVAL, "b0200", "para",
                 "Vi vill fördubbla antalet poliser i yttre tjänst under mandatperioden.",
                 [("V1", "KD"), ("V3", "S")], citations=3),
        ]

    def test_the_totals_count_every_cited_block(self, rows):
        rep = anchors.navigation_report("r", rows=rows)
        assert rep["totals"]["blocks"] == 4
        assert rep["totals"]["citations"] == 7

    def test_a_vote_citing_two_blocks_is_one_vote_and_two_block_votes(self, rows):
        """Both numbers are reported because both get used, and they differ."""
        rep = anchors.navigation_report("r", rows=rows)
        assert rep["totals"]["decisions"] == 3  # V1/KD, V2/KD, V3/S
        assert rep["totals"]["block_decisions"] == 6

    def test_the_role_breakdown_covers_every_cited_block(self, rows):
        rep = anchors.navigation_report("r", rows=rows)
        assert rep["by_role"]["h3"]["blocks"] == 2
        assert rep["by_role"]["label"]["blocks"] == 1
        assert rep["by_role"]["para"]["blocks"] == 1

    def test_role_alone_would_flag_the_pledge_list(self, rows):
        """`label`/`toc` is the plan's literal question and the wrong answer."""
        rep = anchors.navigation_report("r", rows=rows)
        assert rep["label_toc"]["blocks"] == 1
        assert rep["label_toc"]["by_party"] == {"S": {"decisions": 1}}

    def test_the_text_test_flags_the_labels_and_spares_the_pledge(self, rows):
        rep = anchors.navigation_report("r", rows=rows)
        nav = rep["navigational"]
        assert nav["blocks"] == 2
        assert [b["block_id"] for b in nav["blocks_listed"]] == ["b0094", "b0095"]
        assert nav["by_document"] == {
            KDVAL: {"blocks": 2, "citations": 3, "decisions": 2, "block_decisions": 3}
        }

    def test_the_party_breakdown_counts_each_vote_once(self, rows):
        """V2/KD cited both labels; it is one party's one vote."""
        rep = anchors.navigation_report("r", rows=rows)
        assert rep["navigational"]["by_party"] == {"KD": {"decisions": 2}}
        assert rep["navigational"]["block_decisions"] == 3

    def test_the_listing_names_the_line_so_the_finding_can_be_read(self, rows):
        rep = anchors.navigation_report("r", rows=rows)
        top = rep["navigational"]["blocks_listed"][0]
        assert top["text"] == "FLER JOBB FLER FÖRETAG"
        assert (top["role"], top["decisions"], top["parties"]) == ("h3", 2, ["KD"])

    def test_a_run_citing_no_navigation_reports_zero_rather_than_nothing(self):
        rows = [_row(KDVAL, "b0200", "para", "En vanlig mening om politik.", [("V1", "KD")])]
        rep = anchors.navigation_report("r", rows=rows)
        assert rep["navigational"]["blocks"] == 0
        assert rep["navigational"]["by_party"] == {}
        assert rep["navigational"]["blocks_listed"] == []

    def test_the_report_is_json_serializable(self, rows):
        """It is written with --out and read back by the findings note."""
        rep = anchors.navigation_report("r", rows=rows)
        assert json.loads(json.dumps(rep, ensure_ascii=False))["run_id"] == "r"


class TestWeakListCandidates:
    """Whether any flagged phrase could go in `blocklist.WEAK_LIST` instead.

    Two tests per phrase, and they answer different questions: what it would
    catch among the quotes that exist, and whether it occurs at all in another
    party's document of the same class — which needs no committed quote to be a
    trap.
    """

    @pytest.fixture
    def run_dir(self, tmp_path):
        sim = tmp_path / "simulations" / "test-run"
        sim.mkdir(parents=True)
        return tmp_path, sim

    def _write(self, sim, rows):
        (sim / "d.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )

    def test_a_phrase_catching_only_its_own_line_is_clean(self, run_dir):
        root, sim = run_dir
        label = "FLER JOBB FLER FÖRETAG"
        self._write(sim, [_decision([label], document="valmanifest")])
        rows = [_row(KDVAL, "b0094", "h3", label, [("V1", "KD")])]
        (c,) = anchors.weak_list_candidates("test-run", root, rows=rows)
        assert (c["catches"], c["false_positives"]) == (1, 0)
        assert (c["words"], c["chars"]) == (4, len(label))

    def test_containment_catches_a_longer_quote_by_another_party(self, run_dir):
        """Matching is bidirectional (`blocklist.py:139`) and `document` is a
        CLASS, so an entry is offered every party's manifesto."""
        root, sim = run_dir
        label = "FLER JOBB FLER FÖRETAG"
        self._write(sim, [
            _decision([label], document="valmanifest"),
            _decision([f"Vi vill ha {label} i hela landet."], parti="M", vid="V2",
                      document="valmanifest"),
        ])
        rows = [_row(KDVAL, "b0094", "h3", label, [("V1", "KD")])]
        (c,) = anchors.weak_list_candidates("test-run", root, rows=rows)
        assert c["catches"] == 2
        assert c["false_positives"] == 1
        assert c["false_positive_quotes"] == ["vi vill ha fler jobb fler företag i hela landet."]

    def test_a_sub_window_of_the_phrase_is_caught_too(self, run_dir):
        """The other direction: agents quote slices of the same line."""
        root, sim = run_dir
        label = "FLER JOBB FLER FÖRETAG"
        self._write(sim, [
            _decision(["FLER JOBB"], parti="M", vid="V2", document="valmanifest"),
        ])
        rows = [_row(KDVAL, "b0094", "h3", label, [("V1", "KD")])]
        (c,) = anchors.weak_list_candidates("test-run", root, rows=rows)
        assert (c["catches"], c["false_positives"]) == (1, 1)

    def test_only_the_same_document_class_is_offered(self, run_dir):
        root, sim = run_dir
        label = "FLER JOBB FLER FÖRETAG"
        self._write(sim, [
            _decision([f"Vi vill ha {label}."], parti="M", vid="V2", document="partiprogram"),
        ])
        rows = [_row(KDVAL, "b0094", "h3", label, [("V1", "KD")])]
        (c,) = anchors.weak_list_candidates("test-run", root, rows=rows)
        assert (c["catches"], c["false_positives"]) == (0, 0)

    def test_a_phrase_occurring_in_another_partys_document_is_a_trap(self, run_dir):
        """The bar `blocklist.py` states, tested against the DOCUMENTS: no
        committed quote has to exist for a future one to be marked."""
        if not (CORPUS_DIR / f"{MVAL}.txt").exists():
            pytest.skip("corpus not extracted")
        root, sim = run_dir
        self._write(sim, [])
        borrowed = "Dödsskjutningar per år"  # a chart label of valmanifest-2022-m
        rows = [_row(KDVAL, "b0094", "h3", borrowed, [("V1", "KD")])]
        (c,) = anchors.weak_list_candidates("test-run", root, rows=rows)
        assert c["other_documents"] == [MVAL]

    def test_a_blank_quote_is_not_offered_to_the_matcher(self, run_dir):
        """`repair-citations` withdrew it; it is not a claim about the corpus."""
        root, sim = run_dir
        self._write(sim, [_decision(["", "FLER JOBB FLER FÖRETAG"], document="valmanifest")])
        rows = [_row(KDVAL, "b0094", "h3", "FLER JOBB FLER FÖRETAG", [("V1", "KD")])]
        (c,) = anchors.weak_list_candidates("test-run", root, rows=rows)
        assert c["catches"] == 1

    def test_only_navigational_lines_are_proposed(self, run_dir):
        root, sim = run_dir
        self._write(sim, [])
        rows = [_row(KDVAL, "b0200", "para", "En vanlig mening.", [("V1", "KD")])]
        assert anchors.weak_list_candidates("test-run", root, rows=rows) == []


RUN = "full-v4"


@pytest.fixture(scope="module")
def rep():
    if not anchors.anchors_dir(RUN).exists():
        pytest.skip("anchors not built (run: uv run aidag build-anchors)")
    return anchors.navigation_report(RUN)


class TestNavigationRuleIsDuplicatedFaithfully:
    """The rule exists twice; the source of both has to say the same thing.

    The output-parity tests below (and `site/tests/corpus.test.mjs`) only catch
    drift that MOVES the corpus-wide count. Adding a word to `_FINITE_VERBS` on
    one side alone does not move it — none of the six flagged blocks contains
    one — so both suites stay green while the two implementations diverge, and
    the next document to be cited is read differently in the report and on the
    page. CLAUDE.md says these lists "must be edited together"; this is what
    holds anyone to it.
    """

    @staticmethod
    def _ts() -> str:
        from aidag.config import REPO_ROOT

        return (REPO_ROOT / "site" / "src" / "lib" / "anchors.ts").read_text(encoding="utf-8")

    def _set(self, name: str) -> set[str]:
        m = re.search(rf"const {name} = new Set\(\s*`([^`]*)`", self._ts())
        assert m, f"{name} is no longer a backtick word list in anchors.ts"
        return set(m.group(1).split())

    def test_the_finite_verbs_match(self):
        assert self._set("FINITE_VERBS") == set(anchors._FINITE_VERBS)

    def test_the_imperative_stems_match(self):
        assert self._set("IMPERATIVE_LEAD") == set(anchors._IMPERATIVE_LEAD)

    def test_the_roles_match(self):
        m = re.search(r"const NAV_ROLES = new Set<DocRole>\(\[([^\]]*)\]", self._ts())
        assert m
        assert set(re.findall(r"'([^']+)'", m.group(1))) == set(anchors.NAV_ROLES)

    def test_the_word_cap_matches(self):
        m = re.search(r"const NAV_MAX_WORDS = (\d+);", self._ts())
        assert m, "NAV_MAX_WORDS is no longer a named constant in anchors.ts"
        assert int(m.group(1)) == anchors.NAV_MAX_WORDS


class TestNavigationParity:
    """The committed finding, held as a ratchet against the real run.

    These are the numbers `docs/topic-label-citations.md` reports and the site
    measured independently in TypeScript (Task 8b: 6 blocks / 84 decisions).
    Two implementations of one rule agreeing on the same corpus is the only
    check there is that the duplication has not drifted.
    """

    def test_the_corpus_wide_navigation_count_is_what_the_site_measured(self, rep):
        nav = rep["navigational"]
        assert nav["blocks"] == 6
        assert nav["block_decisions"] == 84
        assert nav["decisions"] == 83  # one vote cited two navigation lines

    def test_role_alone_would_have_claimed_the_pledge_lists(self, rep):
        """1,165 block-votes against 84 — the gap IS the finding."""
        assert rep["label_toc"]["block_decisions"] == 1165
        assert rep["role_only"]["block_decisions"] == 1433

    def test_the_finding_is_a_fact_about_two_parties_documents(self, rep):
        assert set(rep["navigational"]["by_document"]) == {
            KDVAL, "partiprogram-v-2016"
        }
        assert rep["navigational"]["by_party"]["KD"]["decisions"] == 81
        assert rep["navigational"]["by_party"]["V"]["decisions"] == 2

    def test_no_declarative_pledge_heading_is_among_them(self, rep):
        """The heading-side false claim, held out by measurement rather than by
        the rule's own unit tests. M sets its headline pledges as `h2` with no
        terminal period, so a punctuation-only text test flagged 10 of them."""
        listed = {b["text"] for b in rep["navigational"]["blocks_listed"]}
        assert not any(t.lower().startswith("vi ska") for t in listed)
        assert MVAL not in rep["navigational"]["by_document"]

    def test_kd_back_cover_labels_are_among_them(self, rep):
        """The finding in this plan's Overview, in the final extraction."""
        kd = [b for b in rep["navigational"]["blocks_listed"] if b["slug"] == KDVAL]
        assert len(kd) == 5
        assert all(b["role"] == "h3" for b in kd)

    def test_every_cited_block_is_accounted_for_by_role(self, rep):
        by_role = rep["by_role"]
        assert sum(a["blocks"] for a in by_role.values()) == rep["totals"]["blocks"]
        assert sum(a["citations"] for a in by_role.values()) == rep["totals"]["citations"]

    def test_no_flagged_phrase_is_disqualified_and_none_is_proposed(self, rep):
        """All 6 pass both tests — and none is proposed anyway. `svag` means
        'supporting but generic', and these quotes are specific; the claim they
        cannot carry is a commitment, which is what the per-block flag says."""
        cands = anchors.weak_list_candidates(RUN)
        assert len(cands) == 6
        # the matcher really ran against the committed record, so "no false
        # positive" is a measurement rather than an empty scan
        assert sum(c["catches"] for c in cands) == 84
        assert all(c["false_positives"] == 0 for c in cands)
        assert all(c["other_documents"] == [] for c in cands)
        from aidag.blocklist import WEAK_LIST

        phrases = {e["phrase"] for e in WEAK_LIST}
        assert not phrases & {c["phrase"] for c in cands}


class TestExportSite:
    def test_blocks_and_both_anchor_halves_land_in_the_site(self, tmp_path, monkeypatch, kd2015):
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)

        n = export_site.export_blocks_and_anchors("test-run")
        assert n == 1
        assert (site / "corpus" / "blocks" / f"{KD2015}.json").exists()
        inline = json.loads((site / "corpus" / "anchors" / f"{KD2015}.json").read_text())
        assert inline["totals"]["refs"] == 1
        fetched = json.loads(
            (tmp_path / "site" / "public" / "data" / "anchors" / f"{KD2015}.json").read_text()
        )
        assert fetched["fields"] == list(anchors.REF_FIELDS)

    def test_a_run_without_anchors_still_exports_the_blocks(self, tmp_path, monkeypatch):
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        assert export_site.export_blocks_and_anchors("no-such-run") == 0
        assert list((site / "corpus" / "blocks").glob("*.json"))

    def test_a_slug_that_stops_being_cited_loses_its_file(self, tmp_path, monkeypatch, kd2015):
        """The run must have an index for this to be the question it sounds like.

        Rebuilt-not-merged is about a run that indexed OTHER slugs than the ones
        on disk. A run with no index at all is the case below, and answering it
        the same way deletes the committed anchors.
        """
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        stale = site / "corpus" / "anchors"
        stale.mkdir(parents=True)
        (stale / "partiprogram-x-1999.json").write_text("{}")
        export_site.export_blocks_and_anchors("test-run")
        assert not (stale / "partiprogram-x-1999.json").exists()
        assert (stale / f"{KD2015}.json").exists()

    def test_a_run_with_no_index_leaves_the_committed_anchors_alone(self, tmp_path, monkeypatch):
        """`export-site --run-id X` before `build-anchors` ever ran for X.

        `load()` cannot distinguish "no index" from "indexed nothing" — both are
        {} — so driving the rebuild off it deletes the committed 46 files and
        ships a site whose document pages have no panels and no rail totals.
        README keeps `mock-v1` as exactly such a run.
        """
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        inline = site / "corpus" / "anchors"
        inline.mkdir(parents=True)
        (inline / f"{KD2015}.json").write_text('{"slug": "kept"}')
        public = tmp_path / "site" / "public" / "data" / "anchors"
        public.mkdir(parents=True)
        (public / f"{KD2015}.json").write_text('{"slug": "kept"}')

        assert export_site.export_blocks_and_anchors("no-such-run") == 0
        assert json.loads((inline / f"{KD2015}.json").read_text())["slug"] == "kept"
        assert json.loads((public / f"{KD2015}.json").read_text())["slug"] == "kept"

    def test_a_run_that_indexed_nothing_still_sweeps(self, tmp_path, monkeypatch):
        """The directory exists and is empty: that IS a run citing nothing, and
        its stale files must go. Only the missing directory is left alone.

        `anchors.build()` refuses to leave such a directory behind (it raises
        `EmptyIndex` before write()), so reaching this state takes a deliberate
        hand — which is what makes honouring it the right answer here.
        """
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        anchors.anchors_dir("empty-run", tmp_path).mkdir(parents=True)
        stale = site / "corpus" / "anchors"
        stale.mkdir(parents=True)
        (stale / f"{KD2015}.json").write_text("{}")
        assert export_site.export_blocks_and_anchors("empty-run") == 0
        assert not (stale / f"{KD2015}.json").exists()

    def test_a_document_that_stops_being_extracted_loses_its_block_file(
        self, tmp_path, monkeypatch
    ):
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        blocks = site / "corpus" / "blocks"
        blocks.mkdir(parents=True)
        (blocks / "partiprogram-x-1999.json").write_text("{}")
        export_site.export_blocks_and_anchors("no-such-run")
        assert not (blocks / "partiprogram-x-1999.json").exists()
        assert (blocks / f"{KD2015}.json").exists()

    def test_an_export_without_a_run_leaves_the_committed_anchors_alone(
        self, tmp_path, monkeypatch
    ):
        """`export-site` with no --run-id is "cases without decisions" (cli.py).

        There is no run to index, so rebuilding the anchor directories from an
        empty payload would delete the committed index — 46 files and every
        document page's rail and panels — with nothing raised anywhere.
        """
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        inline = site / "corpus" / "anchors"
        inline.mkdir(parents=True)
        (inline / f"{KD2015}.json").write_text('{"slug": "kept"}')
        public = tmp_path / "site" / "public" / "data" / "anchors"
        public.mkdir(parents=True)
        (public / f"{KD2015}.json").write_text('{"slug": "kept"}')

        assert export_site.export_blocks_and_anchors(None) == 0
        assert json.loads((inline / f"{KD2015}.json").read_text())["slug"] == "kept"
        assert json.loads((public / f"{KD2015}.json").read_text())["slug"] == "kept"
        # the blocks are still refreshed — they are run-independent
        assert (site / "corpus" / "blocks" / f"{KD2015}.json").exists()


class TestDrawnSpans:
    """`offset`/`length` index the block AS DRAWN, and the corpus proves it matters.

    The served text is the exact bytes the agent read, invisible characters
    included, and `verify simulate` checks every citation against it — so nothing
    may clean it up. The page draws it through `doctext.repairChars`, which
    deletes those characters. A span measured on one and used against the other
    is off by however many the block carries, and `valmanifest-2022-s` carries a
    literal BEL after 40 of its bullet glyphs: measuring on the served text put
    every anchor in those lines one character late, reading "raftigt öka antalet
    poliser" for "Kraftigt …".

    Nothing on the site reads these spans today, which is exactly why this is
    tested here rather than left to be noticed: they are a committed artifact
    with a stated contract.
    """

    S = "valmanifest-2022-s"

    def test_the_bel_is_still_in_the_corpus_this_guards(self):
        """A guard for a character that has gone away tests nothing."""
        idx = _index(self.S)
        assert "\x07" in idx.served
        assert "\x07" not in idx.drawn_served

    def test_a_span_recovers_its_quote_from_the_drawn_block(self):
        idx = _index(self.S)
        payload = anchors.load(RUN).get(self.S)
        if payload is None:
            pytest.skip("anchors not built (run: uv run aidag build-anchors)")
        blocks = {bid: i for i, bid in enumerate(idx.ids)}
        checked = 0
        for a in payload["anchors"]:
            i = blocks[a["block_id"]]
            block = anchors.drawn(idx.served[idx.starts[i] : idx.ends[i]])
            span = block[a["offset"] : a["offset"] + a["length"]]
            if len(span) < a["length"]:
                continue  # the quote runs past this block into the next one
            assert span == anchors.drawn(a["quote"])
            checked += 1
        assert checked > 100, f"only {checked} spans were block-internal"

    def test_every_document_agrees_across_the_committed_index(self):
        payloads = anchors.load(RUN)
        if not payloads:
            pytest.skip("anchors not built (run: uv run aidag build-anchors)")
        bad, checked = [], 0
        for slug, payload in payloads.items():
            idx = _index(slug)
            at = {bid: i for i, bid in enumerate(idx.ids)}
            for a in payload["anchors"]:
                i = at[a["block_id"]]
                start = idx.drawn_starts[i]
                span = idx.drawn_served[start + a["offset"] : start + a["offset"] + a["length"]]
                checked += 1
                if span != anchors.drawn(a["quote"]):
                    bad.append(f"{slug}/{a['block_id']}: {span[:40]!r}")
        assert bad[:5] == []
        assert checked > 10000, f"only {checked} anchors checked"

    def test_drawn_collapses_after_removing_rather_than_before(self):
        """Deleting a character from between two spaces must not leave two."""
        assert anchors.drawn("a \x07 b") == "a b"
        assert anchors.drawn("• \x07Kraftigt") == "• Kraftigt"
        assert anchors.drawn("Vi vill se ett tryggt Sverige.") == "Vi vill se ett tryggt Sverige."


class TestStaleAnchorGuard:
    """Blocks and anchors are two passes; the export must not ship a mismatch.

    `build-anchors` asserts the pairing, but `export-site` does not go through
    it — re-extract, export before re-indexing, and block ids (positional, per
    `docx.assign_roles`) have shifted underneath a standing index.
    """

    def _site(self, tmp_path, monkeypatch):
        from aidag import export_site

        site = tmp_path / "site" / "src" / "data"
        site.mkdir(parents=True)
        monkeypatch.setattr(export_site, "SITE_DATA_DIR", site)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        return export_site, site

    def test_an_index_naming_a_block_the_extraction_does_not_have_is_refused(
        self, tmp_path, monkeypatch, kd2015
    ):
        export_site, _ = self._site(tmp_path, monkeypatch)
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        # the shift a re-extraction produces: the same quote, a block id past the
        # end of the document it is indexed against
        by_slug[KD2015][0].block_id = "b9999"
        anchors.write("test-run", by_slug, tmp_path)
        with pytest.raises(export_site.StaleAnchors, match="b9999"):
            export_site.export_blocks_and_anchors("test-run")

    def test_an_index_for_a_document_with_no_blocks_is_refused(
        self, tmp_path, monkeypatch, kd2015
    ):
        export_site, _ = self._site(tmp_path, monkeypatch)
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        (anchors.anchors_dir("test-run", tmp_path) / "partiprogram-x-1999.json").write_text(
            json.dumps({"slug": "partiprogram-x-1999", "run_id": "test-run",
                        "n_anchors": 0, "n_refs": 0,
                        "anchors": [{"quote": "q", "block_id": "b0001", "offset": 0,
                                     "length": 1, "refs": []}]}),
            encoding="utf-8",
        )
        with pytest.raises(export_site.StaleAnchors, match="partiprogram-x-1999"):
            export_site.export_blocks_and_anchors("test-run")

    def test_a_shift_that_leaves_every_id_in_range_is_still_refused(
        self, tmp_path, monkeypatch, kd2015
    ):
        """The case an id-existence check would wave through.

        A block inserted early renumbers the rest without pushing any id off the
        end, so every anchor still names a block that exists — a different one.
        """
        export_site, _ = self._site(tmp_path, monkeypatch)
        bid, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        monkeypatch.setattr(anchors, "RESULTS_DIR", tmp_path)
        # the same anchor against its neighbour, which is what renumbering does
        other = kd2015.ids[kd2015.ids.index(bid) + 1]
        path = anchors.anchors_dir("test-run", tmp_path) / f"{KD2015}.json"
        payload = json.loads(path.read_text())
        payload["anchors"][0]["block_id"] = other
        payload["anchors"][0]["offset"] = 0
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(export_site.StaleAnchors, match="moved in"):
            export_site.export_blocks_and_anchors("test-run")

    def test_an_anchor_that_no_longer_fits_its_block_is_refused(
        self, tmp_path, monkeypatch, kd2015
    ):
        """The case the cross-block exemption used to swallow.

        143 of full-v4's anchors quote past their block into the next one and so
        read back a SHORT span. An anchor whose block shrank under it reads short
        too, and one whose offset fell off the end reads empty — so exempting
        every short span exempts the drift. Point one at the shortest block in the
        document and it must not pass.
        """
        export_site, _ = self._site(tmp_path, monkeypatch)
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        drawn_blocks = {
            bid: anchors.drawn(kd2015.served[kd2015.starts[i] : kd2015.ends[i]])
            for i, bid in enumerate(kd2015.ids)
        }
        shortest = min(drawn_blocks, key=lambda bid: len(drawn_blocks[bid]))
        path = anchors.anchors_dir("test-run", tmp_path) / f"{KD2015}.json"
        payload = json.loads(path.read_text())
        a = payload["anchors"][0]
        assert a["length"] > len(drawn_blocks[shortest])
        assert not anchors.drawn(a["quote"]).startswith(drawn_blocks[shortest])
        a["block_id"], a["offset"] = shortest, 0
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(export_site.StaleAnchors, match="moved in"):
            export_site.export_blocks_and_anchors("test-run")

    def test_a_cross_block_quote_still_passes(self, tmp_path, monkeypatch, kd2015):
        """Tightening the short-span case must not fail the 143 real ones.

        A quote running into the next block starts with the whole tail of this
        one, which is what separates it from an anchor that has drifted.
        """
        export_site, _ = self._site(tmp_path, monkeypatch)
        bid, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        path = anchors.anchors_dir("test-run", tmp_path) / f"{KD2015}.json"
        payload = json.loads(path.read_text())
        a = payload["anchors"][0]
        i = kd2015.ids.index(bid)
        block = anchors.drawn(kd2015.served[kd2015.starts[i] : kd2015.ends[i]])
        # the quote as the next block continues it: this block's tail, then more
        tail = block[-12:].lstrip()
        a["offset"] = len(block) - len(tail)
        a["quote"] = tail + " och mer text"
        a["length"] = len(anchors.drawn(a["quote"]))
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert export_site.export_blocks_and_anchors("test-run") == 1

    def test_a_block_refresh_under_a_standing_index_is_refused(self, tmp_path, monkeypatch):
        """`extract-corpus --force` then `export-site` with no run to re-check it.

        The blocks are run-independent and refresh on every export; the committed
        anchors index their positional ids and do not. The no-index branches
        return before the pairing guard, so without this they ship a new
        extraction against the previous run's anchors.
        """
        export_site, site = self._site(tmp_path, monkeypatch)
        blocks = site / "corpus" / "blocks"
        blocks.mkdir(parents=True)
        (blocks / f"{KD2015}.json").write_text('{"blocks": []}', encoding="utf-8")
        inline = site / "corpus" / "anchors"
        inline.mkdir(parents=True)
        (inline / f"{KD2015}.json").write_text('{"slug": "kept"}', encoding="utf-8")
        with pytest.raises(export_site.StaleAnchors, match=KD2015):
            export_site.export_blocks_and_anchors(None)
        # Refusing has to happen BEFORE the copy. The guard's evidence is "the
        # site copy differs from the source", so a refusal that had already
        # landed the refresh would leave a second export finding them equal,
        # passing, and shipping the very pairing this raised on.
        assert (blocks / f"{KD2015}.json").read_text() == '{"blocks": []}'
        with pytest.raises(export_site.StaleAnchors, match=KD2015):
            export_site.export_blocks_and_anchors("no-such-run")
        assert (blocks / f"{KD2015}.json").read_text() == '{"blocks": []}'

    def test_a_slug_the_extraction_dropped_is_refused_under_a_standing_index(
        self, tmp_path, monkeypatch
    ):
        """Removal is the extreme of the same drift, not a different case.

        `sync_blocks()` unlinks a site block file the extraction no longer
        produces, so without this the export ships a document whose committed
        anchor file survives with every id it names gone — the page silently
        drops to `formatCorpusDoc()` while the orphaned index stays in the repo.
        The indexed path already refuses it; these branches must not be laxer.
        """
        export_site, site = self._site(tmp_path, monkeypatch)
        gone = "partiprogram-x-1999"  # no such file under data/corpus/blocks/
        blocks = site / "corpus" / "blocks"
        blocks.mkdir(parents=True)
        (blocks / f"{gone}.json").write_text('{"blocks": []}', encoding="utf-8")
        inline = site / "corpus" / "anchors"
        inline.mkdir(parents=True)
        (inline / f"{gone}.json").write_text('{"slug": "kept"}', encoding="utf-8")
        for run in (None, "no-such-run"):
            with pytest.raises(export_site.StaleAnchors, match=gone):
                export_site.export_blocks_and_anchors(run)
            # refused before the copy, so the pair the guard raised on is intact
            assert (blocks / f"{gone}.json").exists()
            assert (inline / f"{gone}.json").exists()

    def test_a_dropped_slug_with_no_standing_index_still_syncs(self, tmp_path, monkeypatch):
        """Nothing indexes it, so unlinking the stale block file is the point."""
        export_site, site = self._site(tmp_path, monkeypatch)
        gone = "partiprogram-x-1999"
        blocks = site / "corpus" / "blocks"
        blocks.mkdir(parents=True)
        (blocks / f"{gone}.json").write_text('{"blocks": []}', encoding="utf-8")
        assert export_site.export_blocks_and_anchors(None) == 0
        assert not (blocks / f"{gone}.json").exists()

    def test_a_slug_the_site_did_not_have_yet_is_not_a_refresh(self, tmp_path, monkeypatch):
        """Adding a block file moves no id, so it must not trip the guard."""
        export_site, site = self._site(tmp_path, monkeypatch)
        inline = site / "corpus" / "anchors"
        inline.mkdir(parents=True)
        (inline / f"{KD2015}.json").write_text('{"slug": "kept"}', encoding="utf-8")
        assert export_site.export_blocks_and_anchors(None) == 0
        assert (site / "corpus" / "blocks" / f"{KD2015}.json").exists()
        assert json.loads((inline / f"{KD2015}.json").read_text())["slug"] == "kept"

    def test_the_committed_export_passes_its_own_guard(self, tmp_path, monkeypatch, kd2015):
        """The guard has to be satisfiable, not just firable."""
        export_site, _ = self._site(tmp_path, monkeypatch)
        _, text = _a_block(kd2015)
        by_slug, _, _, _ = anchors.collect([_decision([text])], CASES, POSITIONS)
        anchors.write("test-run", by_slug, tmp_path)
        assert export_site.export_blocks_and_anchors("test-run") == 1

    def test_run_guards_the_blocks_before_it_writes_the_derived_text(self):
        """`export_corpus()` writes into the directory the guard protects.

        It rewrites all 40 `site/src/data/corpus/*.txt`, and for the 23 extracted
        documents those are DERIVED from the blocks. Called first, a
        `StaleAnchors` refusal leaves the site tree holding the new text beside
        the previous extraction's blocks and anchors — which `git status` shows
        as an ordinary diff and README's publish recipe (`git add site/src/data`)
        takes wholesale. Asserted on the source because `run()` needs the whole
        data tree to reach these two lines, and the ordering is the invariant
        `export_blocks_and_anchors`'s docstring states.
        """
        import ast
        import inspect
        import textwrap

        from aidag import export_site

        tree = ast.parse(textwrap.dedent(inspect.getsource(export_site.run)))
        called: list[str] = []

        class _InOrder(ast.NodeVisitor):
            # not ast.walk: that is breadth-first, and the assertion below is
            # about source order
            def visit_Call(self, node):
                if isinstance(node.func, ast.Name):
                    called.append(node.func.id)
                self.generic_visit(node)

        _InOrder().visit(tree)
        assert "export_corpus" in called and "export_blocks_and_anchors" in called
        assert called.index("export_blocks_and_anchors") < called.index("export_corpus")
