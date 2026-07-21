"""Tests for the reservation-substance layer: parser, scrub, de-leak validation,
ingest idempotency, verify, and the batch-workflow guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aidag.reservations import (
    _match_bodies,
    ingest,
    load_reservations,
    parse_reservations,
    scrub_substance,
    validate_reservation,
    verify_reservations,
)

WORKFLOW_JS = Path(__file__).parent.parent / "scripts" / "reservations_batch_workflow.js"

# A minimal betänkande fulltext with two reservations in the section format
# (heading -> "N. rubrik av <author> (parti). Förslag till riksdagsbeslut …
# Ställningstagande <argument>"), plus an appendix that must not bleed in.
FULLTEXT = (
    "Utskottets överväganden … Reservationer "
    "Reservation 1 (C) 1. A-kassan av Anna Andersson m.fl. (C). "
    "Förslag till riksdagsbeslut Riksdagen ställer sig bakom det som anförs. "
    "Ställningstagande Jag anser att arbetslöshetsförsäkringen behöver reformeras med fokus på omställning. "
    "Reservation 2 (V) 2. Sjunde AP-fonden av Bo Bengtsson (V). "
    "Förslag till riksdagsbeslut Riksdagen ställer sig bakom detta. "
    "Ställningstagande Vänsterpartiet menar att fonden inte ska investera i fossil energi. "
    "Bilaga 1 Förteckning över behandlade förslag"
)


class TestParser:
    reservations = parse_reservations(FULLTEXT)

    def test_extracts_both(self):
        assert len(self.reservations) == 2

    def test_author_parties(self):
        assert self.reservations[0]["author_parti"] == ["C"]
        assert self.reservations[1]["author_parti"] == ["V"]

    def test_body_is_the_argument(self):
        assert self.reservations[0]["stallningstagande"].startswith("Jag anser att arbetslöshetsförsäkringen")

    def test_body_does_not_bleed_into_next_reservation(self):
        # reservation 1's body must not swallow reservation 2's heading/argument
        b0 = self.reservations[0]["stallningstagande"]
        assert "Reservation 2" not in b0
        assert "fossil energi" not in b0

    def test_appendix_excluded(self):
        assert "Förteckning" not in self.reservations[1]["stallningstagande"]

    def test_no_reservations_when_absent(self):
        assert parse_reservations("A committee report with no reservations at all.") == []


class TestScrub:
    def test_strips_party_tag_and_name(self):
        s = scrub_substance("Vänsterpartiet anser att X. Se motion 2022/23:11 av Bo (V).")
        assert "Vänsterpartiet" not in s and "(V)" not in s
        assert "2022/23:11" not in s

    def test_keeps_substance(self):
        s = scrub_substance("Jag anser att a-kassan bör reformeras för omställning.")
        assert "a-kassan" in s and "omställning" in s


class TestMatchBodies:
    def test_pairs_by_author_party(self):
        reservations = [
            {"alt_id": "res-2", "source_partier": ["V"]},
            {"alt_id": "res-1", "source_partier": ["C"]},
        ]
        parsed = [{"author_parti": ["C"], "stallningstagande": "c body"},
                  {"author_parti": ["V"], "stallningstagande": "v body"}]
        pairs = _match_bodies(reservations, parsed)
        assert pairs[0][1]["stallningstagande"] == "v body"  # res-2 (V) -> V body
        assert pairs[1][1]["stallningstagande"] == "c body"  # res-1 (C) -> C body

    def test_order_fallback_when_no_party_match(self):
        reservations = [{"alt_id": "res-1", "source_partier": []}]
        parsed = [{"author_parti": ["M"], "stallningstagande": "only body"}]
        assert _match_bodies(reservations, parsed)[0][1]["stallningstagande"] == "only body"

    def test_none_when_nothing_parsed(self):
        assert _match_bodies([{"alt_id": "res-1", "source_partier": ["C"]}], [])[0][1] is None


def rec(**overrides) -> dict:
    r = {
        "votering_id": "v1", "alt_id": "res-1",
        "subject": {"sv": "Motförslaget vill reformera a-kassan.",
                    "en": "The counter-proposal wants to reform unemployment insurance."},
    }
    return r | overrides


class TestValidate:
    def test_accepts_clean(self):
        validate_reservation(rec())

    def test_rejects_party_tag(self):
        with pytest.raises(ValueError, match="party tag"):
            validate_reservation(rec(subject={"sv": "Förslag från (V).", "en": "x"}))

    def test_rejects_party_name(self):
        with pytest.raises(ValueError, match="party name"):
            validate_reservation(rec(subject={"sv": "Vänsterpartiet vill X.", "en": "x"}))

    def test_rejects_outcome(self):
        with pytest.raises(ValueError, match="outcome word"):
            validate_reservation(rec(subject={"sv": "Riksdagen biföll förslaget.", "en": "x"}))

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="empty subject.en"):
            validate_reservation(rec(subject={"sv": "x", "en": " "}))


class TestIngestVerify:
    """Uses a real reservation key from cases.parquet so alignment passes."""

    @pytest.fixture
    def real_key(self):
        import polars as pl
        from aidag.config import PROCESSED_DIR
        from aidag.reservations import _reservations_of

        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True):
            res = _reservations_of(c)
            if res:
                return c["votering_id"], res[0]["alt_id"]
        pytest.skip("no reservation in cases.parquet")

    def _setup(self, tmp_path, monkeypatch):
        import aidag.reservations as m

        monkeypatch.setattr(m, "cases_path", lambda: tmp_path / "cases.jsonl")

    def test_ingest_and_idempotent(self, tmp_path, monkeypatch, real_key):
        self._setup(tmp_path, monkeypatch)
        vid, alt = real_key
        result = tmp_path / "result.json"
        payload = {"reservations": [rec(votering_id=vid, alt_id=alt)]}
        result.write_text(json.dumps(payload, ensure_ascii=False))
        ingest(str(result), model="claude-haiku-4-5")
        assert len(load_reservations()) == 1
        ingest(str(result), model="claude-haiku-4-5")  # re-ingest adds 0
        assert len(load_reservations()) == 1
        checks = list(verify_reservations())
        assert all(ok for _, ok, _ in checks), checks

    def test_ingest_skips_unknown_and_leaky(self, tmp_path, monkeypatch, real_key):
        self._setup(tmp_path, monkeypatch)
        vid, alt = real_key
        payload = {"reservations": [
            rec(votering_id="NOT-REAL", alt_id="res-1"),                       # unknown
            rec(votering_id=vid, alt_id=alt, subject={"sv": "Från (SD).", "en": "x"}),  # leaky
        ]}
        result = tmp_path / "result.json"
        result.write_text(json.dumps(payload, ensure_ascii=False))
        ingest(str(result), model="claude-haiku-4-5")
        assert load_reservations() == {}

    def test_verify_flags_planted_leak(self, tmp_path, monkeypatch, real_key):
        self._setup(tmp_path, monkeypatch)
        vid, alt = real_key
        planted = {"votering_id": vid, "alt_id": alt,
                   "subject": {"sv": "Motförslaget vill (V).", "en": "x"},
                   "model": "m", "collected_at": "t"}
        (tmp_path / "cases.jsonl").write_text(json.dumps(planted, ensure_ascii=False) + "\n")
        checks = list(verify_reservations())
        valid = next(c for c in checks if c[0].startswith("reservation summaries valid"))
        assert valid[1] is False


class TestWorkflowScript:
    src = WORKFLOW_JS.read_text()

    def test_fail_fast_on_reservations(self):
        assert "args.manifestPath.includes('reservations')" in self.src
        assert "args.manifestPath.includes('translate')" not in self.src

    def test_schema_fields_and_haiku(self):
        for f in ("votering_id", "alt_id", "subject"):
            assert f in self.src
        assert "model: 'haiku'" in self.src

    def test_manifest_drops_run_id_and_kind(self):
        assert "run_id: {" not in self.src and "'run_id'" not in self.src
        assert "kind: {" not in self.src
