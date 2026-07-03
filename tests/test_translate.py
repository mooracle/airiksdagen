"""Alignment guards for the English-translation stage."""

from pathlib import Path

import pytest

from aidag.translate import (
    _case_unit,
    _decision_unit,
    _pack,
    validate_case_translation,
    validate_decision_translation,
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
