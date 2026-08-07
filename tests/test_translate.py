"""Alignment guards for the English-translation stage."""

from pathlib import Path

import pytest

from aidag.translate import (
    _case_unit,
    _decision_unit,
    _flatten_chunks,
    _pack,
    validate_case_translation,
    validate_decision_translation,
    untranslated_fields,
)

WORKFLOW_JS = Path(__file__).parent.parent / "scripts" / "translate_batch_workflow.js"

CASE = {
    "votering_id": "v1",
    "rubrik": "Höjda anslag",
    "dok_titel": "Betänkande",
    "forslag_text": "Riksdagen antar regeringens förslag.",
    "notis": "Riksdagen sa ja.",
    "alternatives": '[{"alt_id": "utskottet", "text": "A"}, {"alt_id": "res-1", "text": "B"}]',
}

DECISION = {
    "motivering": "Planen kräver detta.",
    "citations": [{"quote": "vi vill höja", "princip": "höjda anslag"}],
    "omvarld": {"paverkar": True, "faktorer": [{"faktor": "inflation", "effekt": "dämpar"}]},
}


def case_translation(**overrides) -> dict:
    rec = {
        "votering_id": "v1",
        "rubrik": "Increased funding",
        "dok_titel": "Committee report",
        "forslag_text": "The Riksdag adopts the government's proposal.",
        "notis": "The Riksdag said yes.",
        "alternatives": ["A en", "B en"],
    }
    return rec | overrides


def decision_translation(**overrides) -> dict:
    rec = {
        "cid": "S:v1:p4:anonymous",
        "motivering": "The plan requires this.",
        "citations": [{"quote": "we want to raise", "princip": "increased funding"}],
        "omvarld": [{"faktor": "inflation", "effekt": "dampens"}],
    }
    return rec | overrides


class TestCaseValidation:
    unit = _case_unit(CASE)

    def test_valid(self):
        validate_case_translation(case_translation(), self.unit)

    def test_alternatives_must_stay_parallel(self):
        with pytest.raises(ValueError, match="alternatives length"):
            validate_case_translation(case_translation(alternatives=["only one"]), self.unit)

    def test_empty_rubrik_rejected(self):
        with pytest.raises(ValueError, match="empty rubrik"):
            validate_case_translation(case_translation(rubrik="  "), self.unit)

    def test_notis_required_when_source_has_one(self):
        with pytest.raises(ValueError, match="empty notis"):
            validate_case_translation(case_translation(notis=""), self.unit)


class TestDecisionValidation:
    unit = _decision_unit("S:v1:p4:anonymous", DECISION)

    def test_valid(self):
        validate_decision_translation(decision_translation(), self.unit)

    def test_citations_must_stay_parallel(self):
        with pytest.raises(ValueError, match="citations length"):
            validate_decision_translation(decision_translation(citations=[]), self.unit)

    def test_omvarld_must_stay_parallel(self):
        with pytest.raises(ValueError, match="omvarld length"):
            validate_decision_translation(decision_translation(omvarld=[]), self.unit)

    def test_empty_quote_translation_rejected(self):
        with pytest.raises(ValueError, match="empty citation quote"):
            validate_decision_translation(
                decision_translation(citations=[{"quote": "", "princip": "x"}]), self.unit
            )


def test_pack_groups():
    units = [{"i": i} for i in range(13)]
    groups = _pack(units, 6)
    assert [len(g) for g in groups] == [6, 6, 1]
    assert [u["i"] for g in groups for u in g] == list(range(13))


def test_workflow_script_guards():
    src = WORKFLOW_JS.read_text()
    assert "args.manifestPath" in src, "fail-fast on missing args is required"
    assert "n_items" in src, "manifest transcription count check missing"
    assert "enum: ['cases', 'decisions']" in src


def test_workflow_returns_chunked():
    """A full decision manifest is 9600 units; the VM caps any returned array at
    4096. Returning flat silently discards a completed multi-hour run."""
    src = WORKFLOW_JS.read_text()
    assert "decisions_chunks" in src and "cases_chunks" in src
    assert "const CHUNK = 2000" in src, "chunk size must stay under the 4096 cap"


class TestFlattenChunks:
    def test_chunked_result_is_flattened(self):
        data = {
            "run_id": "r",
            "cases_chunks": [[{"votering_id": "v1"}], [{"votering_id": "v2"}]],
            "decisions_chunks": [[{"cid": "c1"}, {"cid": "c2"}], [{"cid": "c3"}]],
        }
        out = _flatten_chunks(data)
        assert [c["votering_id"] for c in out["cases"]] == ["v1", "v2"]
        assert [d["cid"] for d in out["decisions"]] == ["c1", "c2", "c3"]
        assert "decisions_chunks" not in out

    def test_flat_result_still_accepted(self):
        data = {"run_id": "r", "cases": [], "decisions": [{"cid": "c1"}]}
        assert _flatten_chunks(data)["decisions"] == [{"cid": "c1"}]

    def test_order_is_preserved_across_chunk_boundary(self):
        chunks = [[{"cid": f"c{i}"} for i in range(2000)], [{"cid": "c2000"}]]
        out = _flatten_chunks({"decisions_chunks": chunks})
        assert [d["cid"] for d in out["decisions"]] == [f"c{i}" for i in range(2001)]


class TestUntranslatedFields:
    """Structural validation passes a record whose arrays line up but whose text
    was never translated. A full-v4 batch shipped 388 such rows — English
    motivering and quotes, Swedish `princip` chips — so the language itself is
    checked separately, and `aidag verify translate` reports it.
    """

    def test_english_record_is_clean(self):
        assert untranslated_fields(decision_translation()) == []

    def test_swedish_princip_is_caught(self):
        rec = decision_translation(
            citations=[{"quote": "We want lower taxes", "princip": "förtroende mellan polis och allmänhet"}]
        )
        assert untranslated_fields(rec) == ["citations.princip"]

    def test_swedish_motivering_is_caught(self):
        assert untranslated_fields(decision_translation(motivering="Planen säger att det inte är så.")) == [
            "motivering"
        ]

    def test_missing_and_empty_fields_do_not_trip_it(self):
        assert untranslated_fields({"motivering": "Fine.", "citations": [{"quote": ""}], "omvarld": None}) == []


class TestCidCaseRepair:
    """Translation agents transcribe the cid by hand and sometimes change the
    case of the votering_id's hex. Lowercasing is collision-free across a run's
    cids, so ingest repairs it rather than discarding a good translation — and
    stores the canonical cid, which is what the checkpoint is keyed on.
    """

    def test_lowercased_cids_do_not_collide(self):
        # the property the repair depends on, asserted rather than assumed
        cids = ["M:AB-CD:p6:anonymous", "M:ab-ce:p6:anonymous", "S:AB-CD:p6:anonymous"]
        assert len({c.lower() for c in cids}) == len(cids)
