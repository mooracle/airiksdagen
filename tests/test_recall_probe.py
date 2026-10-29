"""Contamination measurement: the recall probe and the no-documents arm.

The probe is only a fair test if the model sees exactly the three fields the p6
prompt also shows (month, committee, title) and nothing that names the vote; the
no-documents arm is only a baseline if it withholds the documents and nothing else.
"""

import json
import re

from aidag import agent_run
from aidag.promptgen import build_system_blocks
from aidag.recall_probe import (
    PROMPT,
    SAMPLE_PATH,
    binomial_tail,
    chance_of_report_number,
    jaccard,
    month_sv,
    report_number,
    sample,
)

CASE = {
    "votering_id": "91110125-72B3-4C4F-8B1A-584C5616EF08",
    "rm": "2022/23",
    "beteckning": "AU10",
    "dok_id": "HA01AU10",
    "datum": "2023-06-07",
    "utskott": "AU",
    "rubrik": "Regeringens lagförslag",
}


class TestProbePrompt:
    def render(self, case=CASE):
        return PROMPT.format(month=month_sv(case["datum"]), utskott=case["utskott"], rubrik=case["rubrik"])

    def test_shows_the_three_p6_fields(self):
        text = self.render()
        assert "juni 2023" in text and "Utskott: AU" in text and "Regeringens lagförslag" in text

    def test_names_nothing_that_identifies_the_vote(self):
        text = self.render()
        assert CASE["beteckning"] not in text  # the answer itself
        assert CASE["dok_id"] not in text and CASE["votering_id"] not in text
        assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
        assert not re.search(r"\b\d{4}/\d{2}:", text)

    def test_forces_a_guess(self):
        # abstention would hide weak recall; the schema has no null and no empty answer
        assert "Ge alltid ditt bästa svar" in self.render()


class TestScoring:
    def test_report_number(self):
        assert report_number("AU10") == 10
        assert report_number("FöU7") == 7
        assert report_number("") is None

    def test_jaccard(self):
        assert jaccard(set(), set()) == 1.0
        assert jaccard({"S", "V"}, {"S"}) == 0.5
        assert jaccard({"MP"}, {"C"}) == 0.0

    def test_binomial_tail(self):
        assert abs(binomial_tail(0, 10, 0.3) - 1.0) < 1e-12
        assert abs(binomial_tail(1, 2, 0.5) - 0.75) < 1e-12
        assert binomial_tail(11, 10, 0.3) == 0.0

    def test_chance_is_one_over_the_session_report_count(self):
        cases = {
            "a": {"rm": "2023/24", "utskott": "AU", "beteckning": "AU4"},
            "b": {"rm": "2023/24", "utskott": "AU", "beteckning": "AU10"},
            "c": {"rm": "2023/24", "utskott": "FiU", "beteckning": "FiU5"},
        }
        assert abs(chance_of_report_number(["a", "c"], cases) - (1 / 10 + 1 / 5) / 2) < 1e-12


class TestSample:
    def test_sample_is_reproducible_and_committed(self):
        # The committed sample is what the published probe results answer; the
        # sampler must still produce it from the committed data.
        assert sample() == json.loads(SAMPLE_PATH.read_text())["votering_ids"]


class TestNoDocumentsArm:
    def test_withholds_every_document(self):
        blocks = build_system_blocks("KD", "2023-06-07", "2022/23", CASE["votering_id"], "p6", documents=False)
        assert len(blocks) == 1
        assert "<valmanifest" not in blocks[0]["text"] and "<partiprogram" not in blocks[0]["text"]
        assert blocks[-1]["cache_control"]["type"] == "ephemeral"

    def test_keeps_the_role_text(self):
        full = build_system_blocks("KD", "2023-06-07", "2022/23", CASE["votering_id"], "p6")
        bare = build_system_blocks("KD", "2023-06-07", "2022/23", CASE["votering_id"], "p6", documents=False)
        assert len(full) > 1
        assert bare[0]["text"] == full[0]["text"]

    def test_arm_is_part_of_the_cid_and_the_system_file(self):
        sims = agent_run._pending("no-such-run", [CASE], prompt_version="p6", arm="nodocs")
        assert sims and all(cid.endswith(":p6:nodocs") for cid, _p, _c in sims)
        name = agent_run._system_filename("KD", CASE, "p6", "nodocs")
        assert name.endswith("-nodocs.txt")
        assert agent_run._system_filename("KD", CASE, "p6") != name
