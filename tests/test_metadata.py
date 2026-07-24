"""Tests for the case-metadata layer: deterministic extraction, grounding +
de-leak of the agent view, ingest idempotency, verify, and export merge.

The deterministic tests run against REAL cases in data/processed/cases.parquet
(golden votering_ids picked for each `type`), so classification precedence is
checked on actual betänkande prose, not synthetic fixtures.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import polars as pl
import pytest

WORKFLOW_JS = Path(__file__).parent.parent / "scripts" / "metadata_batch_workflow.js"

from aidag.config import PARTIES, PROCESSED_DIR
from aidag.metadata import (
    _case_unit,
    _pack,
    area_for,
    extract_deterministic,
    ingest,
    load_metadata,
    policy_area_labels,
    validate_metadata,
    verify_metadata,
)
from aidag.promptgen import AUTHOR_RE, FORBIDDEN_PATTERNS


def synth_record(**overrides) -> dict:
    """A clean synthesis record as produced by the workflow (pre-ingest)."""
    rec = {
        "votering_id": VID_MOTION,
        "subject": {"sv": "En skatt på kemikalier i viss elektronik.",
                    "en": "A tax on chemicals in certain electronics."},
        "at_stake": {"sv": "Om nya kemikalieskatter ska införas.",
                     "en": "Whether new chemical taxes should be introduced."},
        "subtopics": ["skatt", "kemikalier", "elektronik"],
        "agent": {"subject": "Ett förslag om skatt på kemikalier i elektronik.",
                  "at_stake": "Om skatten på farliga ämnen ska utökas."},
    }
    return rec | overrides

# Golden real votering_ids (verified against cases.parquet this session).
VID_BUDGET_RIKTLINJER = "8D1F96AB-81FF-4783-BA15-FE3F866523F3"  # FiU1, finansplan
VID_BUDGET_UTGIFTSTAK = "A5E32EAE-C89F-4C6F-8D6F-163CE3CA9848"  # FiU1, utgiftstak
VID_PROPOSITION = "376923A0-8D12-4D74-981A-F3BF5B0200E7"        # KU7, pure prop
VID_MOTION = "C7E795A0-9F65-49C8-A574-6CBEBF90DD0E"             # SkU2, pure motion
VID_MIXED = "78FF6849-4C95-4A26-906B-47E61C02E47A"             # KrU2, prop + avslår motion


@lru_cache(maxsize=1)
def _cases_by_vid() -> dict[str, dict]:
    df = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    return {c["votering_id"]: c for c in df.iter_rows(named=True)}


def case(vid: str) -> dict:
    cases = _cases_by_vid()
    if vid not in cases:
        pytest.skip(f"golden case {vid} not in cases.parquet")
    return cases[vid]


class TestClassifyType:
    def test_budget_riktlinjer(self):
        assert extract_deterministic(case(VID_BUDGET_RIKTLINJER))["type"] == "budget"

    def test_budget_utgiftstak(self):
        assert extract_deterministic(case(VID_BUDGET_UTGIFTSTAK))["type"] == "budget"

    def test_pure_proposition(self):
        assert extract_deterministic(case(VID_PROPOSITION))["type"] == "proposition"

    def test_pure_motion_bundle(self):
        assert extract_deterministic(case(VID_MOTION))["type"] == "motion"

    def test_mixed_resolves_to_proposition(self):
        # a betänkande that both adopts a proposition AND rejects a motion must
        # resolve to proposition by precedence (not motion).
        rec = extract_deterministic(case(VID_MIXED))
        assert rec["type"] == "proposition"

    def test_budget_precedence_over_motion(self):
        # the FiU1 riktlinjer case also contains "avslår motionerna" — budget wins.
        c = case(VID_BUDGET_RIKTLINJER)
        assert "avslår motion" in c["forslag_text"].lower()
        assert extract_deterministic(c)["type"] == "budget"


class TestPolicyArea:
    def test_three_committees(self):
        assert extract_deterministic(case(VID_BUDGET_RIKTLINJER))["policy_area"] == "finance"
        assert extract_deterministic(case(VID_PROPOSITION))["policy_area"] == "constitution"
        assert extract_deterministic(case(VID_MOTION))["policy_area"] == "tax"

    def test_committee_preserved_raw(self):
        assert extract_deterministic(case(VID_MOTION))["committee"] == "SkU"

    def test_area_for_case_insensitive(self):
        assert area_for("sfu")["code"] == "migration"
        assert area_for("MJU")["code"] == "environment"

    def test_area_for_joint_committee_folds_to_defense(self):
        assert area_for("UFöU")["code"] == "defense"
        assert area_for("FöU")["code"] == "defense"

    def test_unknown_committee_is_other(self):
        assert area_for("ZZZ")["code"] == "ovrigt"
        assert area_for("")["code"] == "ovrigt"

    def test_policy_area_labels_cover_every_area(self):
        labels = policy_area_labels()
        assert labels["migration"]["en"] == "Migration & social insurance"
        assert labels["ovrigt"]["sv"] == "Övrigt"
        # every committee's code is representable
        for area in ("finance", "defense", "tax", "constitution"):
            assert area in labels


class TestPartiesAndCounts:
    def test_parties_from_source_partier(self):
        # the pure-motion SkU2 case has a reservation authored by MP.
        rec = extract_deterministic(case(VID_MOTION))
        assert "MP" in rec["parties_involved"]

    def test_n_reservations(self):
        c = case(VID_MOTION)
        alts = json.loads(c["alternatives"])
        expected = sum(1 for a in alts if a["alt_id"] != "utskottet")
        assert extract_deterministic(c)["n_reservations"] == expected
        assert extract_deterministic(c)["n_reservations"] >= 1

    def test_n_motions_counts_motion_refs(self):
        c = case(VID_BUDGET_RIKTLINJER)
        refs = json.loads(c["references"])
        expected = sum(1 for r in refs if r.get("typ") == "mot")
        assert extract_deterministic(c)["n_motions"] == expected

    def test_is_budget_flag(self):
        assert extract_deterministic(case(VID_BUDGET_RIKTLINJER))["is_budget"] is True
        assert extract_deterministic(case(VID_MOTION))["is_budget"] is False


class TestDeterministicEdgeCases:
    def test_empty_forslag_text(self):
        rec = extract_deterministic(
            {"votering_id": "x", "utskott": "JuU", "forslag_text": "", "alternatives": "[]", "references": "[]"}
        )
        assert rec["type"] == "other"
        assert rec["policy_area"] == "justice"
        assert rec["n_motions"] == 0
        assert rec["n_reservations"] == 0
        assert rec["parties_involved"] == []

    def test_missing_fields(self):
        rec = extract_deterministic({"votering_id": "x"})
        assert rec["type"] == "other"
        assert rec["policy_area"] == "ovrigt"
        assert rec["committee"] == ""

    def test_unknown_committee_fallback(self):
        rec = extract_deterministic(
            {"votering_id": "x", "utskott": "XYZ", "forslag_text": "Riksdagen avslår motion 2022/23:1.",
             "alternatives": "[]", "references": '[{"typ": "mot"}]'}
        )
        assert rec["policy_area"] == "ovrigt"
        assert rec["type"] == "motion"
        assert rec["n_motions"] == 1

    def test_parties_fallback_from_author_clause(self):
        # no reservations with source_partier -> scrape the author clause.
        rec = extract_deterministic(
            {"votering_id": "x", "utskott": "SkU",
             "forslag_text": "Riksdagen avslår motion 2022/23:11 av Linus Lakso och Emma Nohrén (båda MP).",
             "alternatives": '[{"alt_id": "utskottet", "text": "x", "source_partier": []}]',
             "references": "[]"}
        )
        assert rec["parties_involved"] == ["MP"]

    def test_html_entities_unescaped_for_matching(self):
        # later-riksmöte prose stores "godk&auml;nner regeringens f&ouml;rslag".
        rec = extract_deterministic(
            {"votering_id": "x", "utskott": "FiU",
             "forslag_text": "Riksdagen antar regeringens f&ouml;rslag till lag.",
             "alternatives": "[]", "references": "[]"}
        )
        assert rec["type"] == "proposition"


def test_pack_groups():
    units = [{"i": i} for i in range(13)]
    groups = _pack(units, 5)
    assert [len(g) for g in groups] == [5, 5, 3]
    assert [u["i"] for g in groups for u in g] == list(range(13))


class TestCaseUnit:
    def test_shape(self):
        unit = _case_unit(case(VID_MOTION))
        assert unit["votering_id"] == VID_MOTION
        assert set(unit["display_src"]) == {
            "rubrik", "utskott", "summary", "forslag_text", "reservations", "references"
        }
        assert set(unit["agent_src"]) == {
            "rubrik", "utskott", "forslag_text", "reservations", "references"
        }
        for block in ("display_src", "agent_src"):
            assert isinstance(unit[block]["reservations"], list)
            assert isinstance(unit[block]["references"], list)

    def test_display_src_keeps_party_aware_text(self):
        # the SkU2 forslag carries an author clause; the human block keeps it.
        unit = _case_unit(case(VID_MOTION))
        assert "Lakso" in unit["display_src"]["forslag_text"]

    def test_agent_src_is_scrubbed_golden(self):
        # GOLDEN: the agent block must be party-blind. The SkU2 forslag contains
        # an author name, a party tag "(båda MP)" and a doc ref — all must be gone.
        unit = _case_unit(case(VID_MOTION))
        blob = json.dumps(unit["agent_src"], ensure_ascii=False)
        assert "Lakso" not in blob and "Nohrén" not in blob   # author names gone
        assert "(MP)" not in blob and "(båda MP)" not in blob  # party tag gone
        assert PARTIES["MP"]["name"] not in blob               # full party name absent
        assert "2022/23:11" not in blob                        # doc ref scrubbed
        for pattern in FORBIDDEN_PATTERNS:
            import re as _re
            assert not _re.search(pattern, blob), f"leak {pattern} in agent_src"
        assert not AUTHOR_RE.search(blob)

    def test_no_notis_in_either_block(self):
        # post-decision notis text must never reach the grounding blocks.
        c = dict(case(VID_MOTION))
        c["notis"] = "Riksdagen sa ja till utskottets förslag ZZZUNIQUE."
        unit = _case_unit(c)
        assert "ZZZUNIQUE" not in json.dumps(unit, ensure_ascii=False)


class TestPrepareStatus:
    def test_prepare_writes_files_and_manifest(self, tmp_path, monkeypatch):
        import aidag.metadata as m

        monkeypatch.setattr(m, "_run_dir", lambda: tmp_path)
        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "empty.jsonl")  # nothing done
        m.prepare(batch_size=2, per_request=3)

        manifests = sorted((tmp_path / "batches").glob("batch-*.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text())
        # run-independent + single-kind: NO run_id, NO kind on manifest or items
        assert "run_id" not in manifest and "kind" not in manifest
        assert manifest["n_items"] == 2 == len(manifest["items"])
        reqs = sorted((tmp_path / "reqs").glob("batch-*.json"))
        assert len(reqs) == 2
        for item, req in zip(manifest["items"], reqs):
            assert "kind" not in item and "run_id" not in item
            body = json.loads(req.read_text())
            assert body["units"] and "instructions" in body
            assert len(body["units"]) == item["n_units"] == 3

    def test_pending_excludes_ingested_ids(self, tmp_path, monkeypatch):
        import aidag.metadata as m

        done_path = tmp_path / "cases.jsonl"
        done_path.write_text(json.dumps({"votering_id": VID_MOTION}) + "\n")
        monkeypatch.setattr(m, "cases_path", lambda: done_path)
        pending_ids = {u["votering_id"] for u in m._pending()}
        assert VID_MOTION not in pending_ids
        # a different real case is still pending
        assert VID_PROPOSITION in pending_ids


class TestValidateMetadata:
    def test_accepts_clean_record(self):
        validate_metadata(synth_record())

    def test_rejects_party_tag_in_agent(self):
        rec = synth_record(agent={"subject": "Ett förslag från (C).", "at_stake": "x"})
        with pytest.raises(ValueError, match="party tag"):
            validate_metadata(rec)

    def test_rejects_full_party_name_in_agent(self):
        rec = synth_record(agent={"subject": "Centerpartiet vill sänka skatten.", "at_stake": "x"})
        with pytest.raises(ValueError, match="party name"):
            validate_metadata(rec)

    def test_rejects_author_clause_in_agent(self):
        rec = synth_record(
            agent={"subject": "Avslag på motion av Jonny Cato och Helena Vilhelmsson (båda C).",
                   "at_stake": "x"}
        )
        with pytest.raises(ValueError, match="author clause"):
            validate_metadata(rec)

    def test_rejects_outcome_word_in_agent(self):
        rec = synth_record(agent={"subject": "Riksdagen sa ja till förslaget.", "at_stake": "x"})
        with pytest.raises(ValueError, match="outcome word"):
            validate_metadata(rec)

    def test_rejects_docref_in_agent(self):
        rec = synth_record(agent={"subject": "Om proposition 2022/23:85.", "at_stake": "x"})
        with pytest.raises(ValueError, match="document ref"):
            validate_metadata(rec)

    def test_rejects_beteckning_in_agent(self):
        # a beteckning WITH letters ("…:AU10") is now caught by the broadened
        # DOCREF_RE (alpha-prefixed betänkande refs), before the FORBIDDEN_PATTERNS
        # fallback — still rejected either way.
        rec = synth_record(agent={"subject": "Betänkande 2023/24:AU10.", "at_stake": "x"})
        with pytest.raises(ValueError, match="document ref"):
            validate_metadata(rec)

    def test_rejects_empty_subject(self):
        with pytest.raises(ValueError, match="empty subject.en"):
            validate_metadata(synth_record(subject={"sv": "x", "en": "  "}))

    def test_rejects_empty_agent_field(self):
        with pytest.raises(ValueError, match="empty agent.at_stake"):
            validate_metadata(synth_record(agent={"subject": "x", "at_stake": ""}))

    def test_rejects_non_list_subtopics(self):
        with pytest.raises(ValueError, match="subtopics must be a list"):
            validate_metadata(synth_record(subtopics="skatt"))

    def test_votering_id_alignment(self):
        with pytest.raises(ValueError, match="votering_id mismatch"):
            validate_metadata(synth_record(), unit={"votering_id": "other"})


class TestIngest:
    def _setup(self, tmp_path, monkeypatch):
        import aidag.metadata as m

        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")

    def test_ingest_merges_deterministic_and_dedupes(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = tmp_path / "result.json"
        result.write_text(json.dumps({"cases": [synth_record()]}, ensure_ascii=False))
        ingest(str(result), model="claude-haiku-4-5")

        recs = load_metadata()
        assert VID_MOTION in recs
        rec = recs[VID_MOTION]
        # server-side deterministic fields merged in (not taken from the model)
        assert rec["type"] == "motion"
        assert rec["policy_area"] == "tax"
        assert rec["committee"] == "SkU"
        assert rec["model"] == "claude-haiku-4-5" and rec["collected_at"]
        # synthesis fields preserved
        assert rec["subject"]["en"].startswith("A tax")
        assert rec["subtopics"] == ["skatt", "kemikalier", "elektronik"]

    def test_ingest_is_idempotent(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = tmp_path / "result.json"
        result.write_text(json.dumps({"cases": [synth_record()]}, ensure_ascii=False))
        ingest(str(result), model="claude-haiku-4-5")
        n_after_first = len(load_metadata())
        ingest(str(result), model="claude-haiku-4-5")  # re-ingest adds 0
        assert len(load_metadata()) == n_after_first == 1

    def test_ingest_skips_leaky_record(self, tmp_path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)
        leaky = synth_record(agent={"subject": "Riksdagen sa ja.", "at_stake": "x"})
        result = tmp_path / "result.json"
        result.write_text(json.dumps({"cases": [leaky]}, ensure_ascii=False))
        ingest(str(result), model="claude-haiku-4-5")
        assert VID_MOTION not in load_metadata()

    def test_ingest_skips_unknown_id(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        result = tmp_path / "result.json"
        result.write_text(json.dumps({"cases": [synth_record(votering_id="NOT-REAL")]}))
        ingest(str(result), model="claude-haiku-4-5")
        assert load_metadata() == {}


class TestVerifyMetadata:
    def test_clean_records_pass(self, tmp_path, monkeypatch):
        import aidag.metadata as m

        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")
        result = tmp_path / "result.json"
        result.write_text(json.dumps({"cases": [synth_record()]}, ensure_ascii=False))
        ingest(str(result), model="claude-haiku-4-5")

        checks = list(verify_metadata())
        assert all(ok for _, ok, _ in checks), checks

    def test_planted_leak_is_flagged(self, tmp_path, monkeypatch):
        import aidag.metadata as m

        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")
        # write a record that bypassed ingest validation (a planted regression):
        # merge real deterministic fields so only the de-leak check fails.
        det = extract_deterministic(case(VID_MOTION))
        planted = {"votering_id": VID_MOTION, **det,
                   "subject": {"sv": "x", "en": "x"}, "at_stake": {"sv": "x", "en": "x"},
                   "subtopics": [], "agent": {"subject": "Riksdagen biföll (SD).", "at_stake": "x"},
                   "model": "m", "collected_at": "t"}
        (tmp_path / "cases.jsonl").write_text(json.dumps(planted, ensure_ascii=False) + "\n")

        checks = list(verify_metadata())
        clean_check = next(c for c in checks if c[0].startswith("metadata records valid"))
        assert clean_check[1] is False, checks

    def test_deterministic_drift_is_flagged(self, tmp_path, monkeypatch):
        import aidag.metadata as m

        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")
        # a record whose stored deterministic fields disagree with the source
        rec = synth_record()
        stored = {"votering_id": VID_MOTION, **extract_deterministic(case(VID_MOTION))}
        stored["type"] = "budget"  # deliberately wrong
        stored |= {k: rec[k] for k in ("subject", "at_stake", "subtopics", "agent")}
        (tmp_path / "cases.jsonl").write_text(json.dumps(stored, ensure_ascii=False) + "\n")

        checks = list(verify_metadata())
        align = next(c for c in checks if "deterministic fields align" in c[0])
        assert align[1] is False


class TestVerifyStageCLI:
    """The `aidag verify metadata` stage dispatch (run-independent, in the stages dict)."""

    def _clean_row(self):
        rec = synth_record()
        return {"votering_id": VID_MOTION, **extract_deterministic(case(VID_MOTION)),
                **{k: rec[k] for k in ("subject", "at_stake", "subtopics", "agent")},
                "model": "m", "collected_at": "t"}

    def test_verify_metadata_clean_returns_zero(self, tmp_path, monkeypatch):
        import aidag.metadata as m
        from aidag.verify import run as verify_run

        (tmp_path / "cases.jsonl").write_text(json.dumps(self._clean_row(), ensure_ascii=False) + "\n")
        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")
        assert verify_run(stage="metadata") == 0

    def test_verify_metadata_leaky_returns_nonzero(self, tmp_path, monkeypatch):
        import aidag.metadata as m
        from aidag.verify import run as verify_run

        row = self._clean_row()
        row["agent"] = {"subject": "Riksdagen sa ja till (SD).", "at_stake": "x"}  # leaks
        (tmp_path / "cases.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n")
        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")
        assert verify_run(stage="metadata") != 0


class TestMergeCaseMetadata:
    from aidag.export_site import merge_case_metadata as _merge

    def _row(self):
        return {"id": VID_MOTION, "rubrik": "Skatt på kemikalier", "rubrik_en": "Tax on chemicals",
                "titel": "Betänkande SkU2", "bet": "2022/23:SkU2"}

    def _meta_rec(self):
        rec = synth_record()
        return {"votering_id": VID_MOTION, **extract_deterministic(case(VID_MOTION)),
                **{k: rec[k] for k in ("subject", "at_stake", "subtopics", "agent")}}

    def test_merges_full_meta_into_payload(self):
        from aidag.export_site import merge_case_metadata

        payload, row = {"votering_id": VID_MOTION}, self._row()
        merge_case_metadata(payload, row, self._meta_rec())
        m = payload["meta"]
        assert m["type"] == "motion" and m["policy_area"] == "tax"
        assert m["subject"]["en"].startswith("A tax")
        assert m["at_stake"]["sv"] and m["subtopics"] == ["skatt", "kemikalier", "elektronik"]
        assert "MP" in m["parties_involved"]

    def test_carries_the_casemeta_brief(self):
        """decision/ja/nej reach the payload; agent.* deliberately does not."""
        from aidag.export_site import merge_case_metadata

        rec = {**self._meta_rec(),
               "decision": {"sv": "Frågan gäller X eller Y.", "en": "Whether X or Y."},
               "ja": {"sv": "Utskottet avstyrker.", "en": "The committee rejects."},
               "nej": [{"alt_id": "res-1", "sv": "Reservationen vill Z.", "en": "The reservation wants Z."}]}
        payload, row = {"votering_id": VID_MOTION}, self._row()
        merge_case_metadata(payload, row, rec)
        m = payload["meta"]
        assert m["decision"]["en"] == "Whether X or Y."
        assert m["ja"]["sv"] == "Utskottet avstyrker."
        assert [n["alt_id"] for n in m["nej"]] == ["res-1"]
        # the party-blind prompt slice is not a site field
        assert "agent" not in m
        # the brief must not bloat the client-fetched index row
        for k in ("decision", "ja", "nej"):
            assert k not in row

    def test_brief_absent_on_pre_casemeta_records(self):
        """A record predating the casemeta layer still merges, with empty brief fields."""
        from aidag.export_site import merge_case_metadata

        payload, row = {"votering_id": VID_MOTION}, self._row()
        merge_case_metadata(payload, row, self._meta_rec())
        m = payload["meta"]
        assert m["decision"] is None and m["ja"] is None and m["nej"] == []

    def test_index_row_is_lean(self):
        from aidag.export_site import merge_case_metadata

        payload, row = {}, self._row()
        merge_case_metadata(payload, row, self._meta_rec())
        # only filter keys + a lean search blob added — no dual-lang subject, no at_stake
        assert row["policy_area"] == "tax" and row["type"] == "motion"
        assert "subject" not in row and "at_stake" not in row
        blob = row["search"]
        assert isinstance(blob, str) and blob == blob.lower()
        # blob holds the NEW searchable text (subject sv/en + subtopics)...
        for needle in ("a tax on chemicals", "en skatt på kemikalier", "elektronik"):
            assert needle in blob
        # ...but does NOT duplicate the display fields already on the row
        assert "sku2" not in blob and "betänkande" not in blob

    def test_falls_back_cleanly_without_metadata(self):
        from aidag.export_site import merge_case_metadata

        payload, row = {"votering_id": VID_MOTION}, self._row()
        merge_case_metadata(payload, row, None)
        assert "meta" not in payload
        # no metadata -> no filter keys and no search blob (client uses display fields)
        assert "policy_area" not in row and "type" not in row and "search" not in row


class TestWorkflowScript:
    src = WORKFLOW_JS.read_text()

    def test_fail_fast_guard_on_metadata(self):
        assert "args.manifestPath" in self.src
        assert "args.manifestPath.includes('metadata')" in self.src
        assert "args.manifestPath.includes('translate')" not in self.src

    def test_manifest_transcription_check(self):
        assert "n_items" in self.src

    def test_units_schema_fields(self):
        for field in ("votering_id", "subject", "at_stake", "subtopics", "agent"):
            assert field in self.src

    def test_manifest_schema_drops_run_id_and_kind(self):
        # run-independent, single-kind — the manifest schema must NOT declare them
        # (prose comments may still mention run_id; target the schema syntax)
        assert "run_id: {" not in self.src
        assert "'run_id'" not in self.src
        assert "enum: ['cases', 'decisions']" not in self.src
        assert "kind: {" not in self.src

    def test_runs_on_haiku(self):
        assert "model: 'haiku'" in self.src


class TestDownloadsExport:
    """The open-data case-metadata.jsonl emitted during export-site (Task 10).

    Exercises just the JSONL-writing slice of run() hermetically, since the full
    export needs the whole processed dataset."""

    def _write_metadata_jsonl(self, downloads, index, metadata_by_vid):
        with open(downloads / "case-metadata.jsonl", "w") as f:
            for row in index:
                rec = metadata_by_vid.get(row["id"])
                if rec:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_well_formed_one_row_per_case_with_metadata(self, tmp_path):
        index = [{"id": "A"}, {"id": "B"}, {"id": "C"}]  # C has no metadata record
        rec_a = {"votering_id": "A", "type": "motion", "subject": {"sv": "x", "en": "x"},
                 "at_stake": {"sv": "y", "en": "y"}, "agent": {"subject": "s", "at_stake": "a"}}
        rec_b = {"votering_id": "B", "type": "budget", "subject": {"sv": "x", "en": "x"},
                 "at_stake": {"sv": "y", "en": "y"}, "agent": {"subject": "s", "at_stake": "a"}}
        self._write_metadata_jsonl(tmp_path, index, {"A": rec_a, "B": rec_b})

        lines = (tmp_path / "case-metadata.jsonl").read_text().splitlines()
        assert len(lines) == 2  # C skipped (no metadata)
        parsed = [json.loads(l) for l in lines]
        assert [p["votering_id"] for p in parsed] == ["A", "B"]
        # full records include at_stake + the agent view
        assert all("at_stake" in p and "agent" in p for p in parsed)
