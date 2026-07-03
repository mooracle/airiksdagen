"""Guards for the subagent full-run path: ingest validation, citation repair,
and keeping the workflow script's schemas in sync with the pipeline's."""

from pathlib import Path

import pytest

from aidag.ingest_agent_run import parse_sim
from aidag.promptgen import DECISION_SCHEMA
from aidag.repair import repair_decision

WORKFLOW_JS = Path(__file__).parent.parent / "scripts" / "agent_batch_workflow.js"

DECISION_PAYLOAD = {
    "rost": "Ja",
    "confidence": "high",
    "coverage": "explicit",
    "motivering": "Testmotivering.",
    "citations": [],
    "omvarld": {"paverkar": False, "faktorer": []},
    "flags": [],
}

KNOWN_VIDS = {"11111111-1111-1111-1111-111111111111"}
GOOD_CID = "S:11111111-1111-1111-1111-111111111111:p4:anonymous"


def _sim(cid: str, **extra) -> dict:
    return {"cid": cid, "decision": dict(DECISION_PAYLOAD), **extra}


class TestParseSim:
    def test_valid(self):
        d = parse_sim(_sim(GOOD_CID), "full-v1", "sonnet", KNOWN_VIDS)
        assert d.parti == "S"
        assert d.arm == "anonymous"
        assert d.prompt_version == "p4"

    def test_unknown_party_rejected(self):
        with pytest.raises(ValueError, match="unknown party"):
            parse_sim(
                _sim("XX:11111111-1111-1111-1111-111111111111:p4:anonymous"),
                "full-v1", "sonnet", KNOWN_VIDS,
            )

    def test_unknown_votering_rejected(self):
        with pytest.raises(ValueError, match="not in cases"):
            parse_sim(
                _sim("S:22222222-2222-2222-2222-222222222222:p4:anonymous"),
                "full-v1", "sonnet", KNOWN_VIDS,
            )

    def test_cid_item_field_mismatch_rejected(self):
        # a transcription typo that desyncs cid from the item fields must not ingest
        with pytest.raises(ValueError, match="disagrees"):
            parse_sim(_sim(GOOD_CID, party="M"), "full-v1", "sonnet", KNOWN_VIDS)

    def test_malformed_cid_rejected(self):
        with pytest.raises(ValueError):
            parse_sim(_sim("S:only-two-parts"), "full-v1", "sonnet", KNOWN_VIDS)


class TestRepairDecision:
    CORPUS = {"valmanifest": "Vi vill sänka skatten på arbete och pension."}

    def test_verbatim_untouched(self):
        d = {"citations": [{"document": "valmanifest", "quote": "sänka skatten på arbete"}], "flags": []}
        assert repair_decision(d, self.CORPUS) == (1, 0, 0)
        assert d["flags"] == []

    def test_paraphrase_repaired_and_flagged(self):
        d = {
            "citations": [{"document": "valmanifest", "quote": "Vi vill sänka skatt på arbete och pensioner."}],
            "flags": [],
        }
        ok, fixed, failed = repair_decision(d, self.CORPUS)
        assert (ok, fixed, failed) == (0, 1, 0)
        assert "citat_korrigerat" in d["flags"]
        assert d["citations"][0]["quote"] in "Vi vill sänka skatten på arbete och pension."

    def test_unknown_document_flagged_not_skipped(self):
        # a citation into a document the agent never had must surface in verify,
        # not silently pass through repair
        d = {"citations": [{"document": "partiprogram", "quote": "något helt annat"}], "flags": []}
        ok, fixed, failed = repair_decision(d, self.CORPUS)
        assert (ok, fixed, failed) == (0, 0, 1)
        assert "citat_ej_verifierat" in d["flags"]


class TestWorkflowScriptSync:
    """The workflow script duplicates pipeline schemas in JS; these tripwires
    catch the copies drifting apart."""

    def test_document_enum_matches(self):
        src = WORKFLOW_JS.read_text()
        assert "'partiprogram'" not in src, "js schema allows citing a document never provided"
        assert "enum: ['valmanifest', 'tidoavtalet']" in src
        py_enum = DECISION_SCHEMA["properties"]["citations"]["items"]["properties"]["document"]["enum"]
        assert py_enum == ["valmanifest", "tidoavtalet"]

    def test_required_fields_match(self):
        src = WORKFLOW_JS.read_text()
        js_required = "', '".join(DECISION_SCHEMA["required"])
        assert f"required: ['{js_required}']" in src

    def test_manifest_count_check_present(self):
        src = WORKFLOW_JS.read_text()
        assert "n_sims" in src and "n_probes" in src, "manifest transcription count check missing"
