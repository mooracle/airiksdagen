"""The stance recheck asks whether the published vote follows from the citations.

Its whole value rests on the judge being blind to what was published, so most of
what is worth testing here is the shape of what the judge is handed — not the
judge's answers, which are a model's.
"""

import json

import pytest

from aidag import stance_recheck as sr

DECISION = {
    "votering_id": "13FA18B9-3512-44C9-B1CD-265A8145CD19",
    "parti": "S",
    "prompt_version": "p6",
    "arm": "anonymous",
    "rost": "Nej",
    "hallning": "stodjer",
    "tier": "explicit",
    "coverage": "explicit",
    "confidence": "high",
    "motivering": "Planen talar därför för att förslaget antas.",
    "citations": [{"document": "valmanifest", "quote": "Sverige ska gå med i Nato.",
                   "princip": "svenskt Nato-medlemskap"}],
    "flags": [],
    "program_override": "strict",
}


class TestTheJudgeIsBlind:
    def test_unit_carries_no_published_conclusion(self):
        u = sr._unit(DECISION)
        assert set(u) == {"cid", "citations", "motivering"}
        blob = json.dumps(u, ensure_ascii=False)
        # `rost` and `hallning` would anchor the re-derivation outright; `tier`,
        # `coverage` and `confidence` are derived from the fields under audit, so
        # they leak the same answer one step removed.
        for leaked in ("hallning", "stodjer", "avvisar", '"rost"', "tier",
                       "coverage", "confidence", "program_override"):
            assert leaked not in blob, leaked

    def test_citations_come_before_the_motivering(self):
        # Payload order reinforces the field order the judge answers in. If the
        # conclusion arrives first, the citations-only judgement is no longer one.
        keys = list(sr._unit(DECISION))
        assert keys.index("citations") < keys.index("motivering")

    def test_cid_round_trips_to_the_decision(self):
        assert sr._unit(DECISION)["cid"] == "S:13FA18B9-3512-44C9-B1CD-265A8145CD19:p6:anonymous"


class TestFieldOrderIsLoadBearing:
    """dict order is emission order, so the citations-only judgement has to be
    committed before the model states what the actor's own prose concluded.
    Reversing these turns the pass into an echo of the motivering."""

    def test_hallning_is_decided_before_motivering_direction(self):
        props = list(sr.VERDICT_SCHEMA["properties"]["units"]["items"]["properties"])
        assert props.index("citations_bear") < props.index("hallning")
        assert props.index("hallning") < props.index("motivering_direction")

    def test_required_order_matches(self):
        req = sr.VERDICT_SCHEMA["properties"]["units"]["items"]["required"]
        assert req.index("hallning") < req.index("motivering_direction")

    def test_hallning_has_the_same_two_values_as_the_p6_schema(self):
        from aidag.promptgen import DECISION_SCHEMA_P6

        mine = sr.VERDICT_SCHEMA["properties"]["units"]["items"]["properties"]["hallning"]["enum"]
        theirs = DECISION_SCHEMA_P6["properties"]["hallning"]["enum"]
        assert mine == theirs

    def test_instructions_tell_the_judge_to_ignore_the_motivering_for_hallning(self):
        # The only firewall `hallning` has. If this wording goes, the finding it
        # produces stops being independent of the prose it is meant to check.
        assert "IGNORE the motivering" in sr.INSTRUCTIONS


class TestPending:
    def test_undecidable_cases_are_skipped_not_judged(self, monkeypatch, capsys):
        # A stance taken against no counter-proposal has nothing for a judge to
        # re-derive from either, so a disagreement there would report a stance
        # defect where the real defect is the prompt.
        monkeypatch.setattr(sr, "load_stances", lambda run_id: {})
        monkeypatch.setattr(sr, "load_decisions", lambda run_id: [DECISION])
        monkeypatch.setattr(sr, "_arende", lambda case, arm="anonymous": None)

        import polars as pl

        monkeypatch.setattr(
            pl, "read_parquet",
            lambda *a, **k: pl.DataFrame([{"votering_id": DECISION["votering_id"], "punkt": 2}]),
        )
        assert sr._pending("full-v4") == []
        assert "undecidable" in capsys.readouterr().out

    def test_already_rechecked_decisions_are_not_reissued(self, monkeypatch):
        cid = sr._cid(DECISION)
        monkeypatch.setattr(sr, "load_stances", lambda run_id: {cid: {"cid": cid}})
        monkeypatch.setattr(sr, "load_decisions", lambda run_id: [DECISION])
        assert sr._pending("full-v4") == []


class TestLoadDecisionsIsP6Only:
    def test_a_p5_decision_has_no_chain_to_recheck(self, tmp_path, monkeypatch):
        # p4/p5 carry `rost` straight from the model with no stance in between, so
        # there is no citations->stance->vote step for this pass to audit.
        run = tmp_path / "simulations" / "r"
        run.mkdir(parents=True)
        p5 = {**DECISION, "prompt_version": "p5"}
        p5.pop("hallning")
        (run / "S.jsonl").write_text(
            json.dumps(p5, ensure_ascii=False) + "\n"
            + json.dumps(DECISION, ensure_ascii=False) + "\n"
        )
        monkeypatch.setattr(sr, "RESULTS_DIR", tmp_path)
        got = sr.load_decisions("r")
        assert [d["prompt_version"] for d in got] == ["p6"]

    def test_a_missing_run_raises_rather_than_reporting_zero(self, tmp_path, monkeypatch):
        # An empty recheck and a typoed run id would otherwise print the same
        # reassuring zeroes.
        monkeypatch.setattr(sr, "RESULTS_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            sr.load_decisions("nope")


class TestPruneRefusesAmbiguousWork:
    def test_pruning_nothing_is_an_error(self):
        from aidag.simulate import prune

        with pytest.raises(ValueError, match="refusing to prune nothing"):
            prune("full-v4")
