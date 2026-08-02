"""agent-merge: fold the JSONL/JSON files the group agents wrote back into the
single {sims} payload agent-ingest consumes. Fully hermetic — no API, no
data/processed — merge only reads a manifest + the out files it points to."""

from __future__ import annotations

import json

from aidag.agent_run import merge


def _decision(cid: str, rost: str = "Ja") -> dict:
    return {
        "cid": cid,
        "rost": rost,
        "confidence": "high",
        "coverage": "explicit",
        "motivering": "kort",
        "citations": [],
        "omvarld": {"paverkar": False, "faktorer": []},
        "flags": [],
    }


def _write_manifest(tmp_path, out_dir, items) -> str:
    manifest = {
        "run_id": "test-run",
        "prompt_version": "p5",
        "n_sims": sum(len(i["cids"]) for i in items),
        "cases_dir": "x/agentrun/cases",
        "groups_dir": "x/agentrun/groups",
        "system_dir": "x/agentrun/system",
        "out_dir": str(out_dir),
        "items": items,
    }
    mpath = tmp_path / "batch-001.json"
    mpath.write_text(json.dumps(manifest))
    return str(mpath)


def test_merge_collects_dedups_and_drops(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Group A (party S) — two clean decisions as JSONL.
    (out_dir / "g-0000-001.jsonl").write_text(
        json.dumps(_decision("S:VID1:p5:anonymous")) + "\n"
        + json.dumps(_decision("S:VID2:p5:anonymous", "Nej")) + "\n"
    )
    # Group B (party M) — mixed: a good one, a hallucinated cid, a truncated line
    # (JSONL part 1) plus a JSON-array part 2 that repeats VID4 (dedup). VID5 is
    # never written (must re-issue, i.e. must be absent from sims).
    (out_dir / "g-0001-001.jsonl").write_text(
        json.dumps(_decision("M:VID3:p5:anonymous")) + "\n"
        + json.dumps(_decision("X:VIDZ:p5:anonymous")) + "\n"  # cid not in group -> drop
        + '{"cid": "M:VID4:p5:anonymo'  # truncated final line -> skipped
    )
    (out_dir / "g-0001-002.json").write_text(
        json.dumps([_decision("M:VID4:p5:anonymous"), _decision("M:VID4:p5:anonymous")])
    )

    items = [
        {"kind": "simgroup", "party": "S", "sys": "S.txt", "gf": "g-0000.json",
         "cids": ["S:VID1:p5:anonymous", "S:VID2:p5:anonymous"]},
        {"kind": "simgroup", "party": "M", "sys": "M.txt", "gf": "g-0001.json",
         "cids": ["M:VID3:p5:anonymous", "M:VID4:p5:anonymous", "M:VID5:p5:anonymous"]},
    ]
    mpath = _write_manifest(tmp_path, out_dir, items)
    out = tmp_path / "merged.json"
    merge(run_id="test-run", manifest_path=mpath, out_path=str(out))

    payload = json.loads(out.read_text())
    assert payload["run_id"] == "test-run"
    assert "probes" not in payload
    by_cid = {s["cid"]: s for s in payload["sims"]}
    # VID1, VID2, VID3, VID4 survive; VID4 deduped to one; VIDZ dropped; VID5 absent
    assert set(by_cid) == {
        "S:VID1:p5:anonymous", "S:VID2:p5:anonymous",
        "M:VID3:p5:anonymous", "M:VID4:p5:anonymous",
    }
    assert len(payload["sims"]) == 4  # no duplicate VID4
    # party/vid derived from the cid; the decision object no longer carries cid
    assert by_cid["S:VID2:p5:anonymous"]["party"] == "S"
    assert by_cid["S:VID2:p5:anonymous"]["vid"] == "VID2"
    assert by_cid["S:VID2:p5:anonymous"]["decision"]["rost"] == "Nej"
    assert "cid" not in by_cid["M:VID3:p5:anonymous"]["decision"]


def test_merge_defaults_to_latest_manifest(tmp_path, monkeypatch):
    """With no --manifest, merge picks the newest batch-*.json under the run dir."""
    import aidag.agent_run as ar

    base = tmp_path / "agentrun" / "test-run"
    (base / "batches").mkdir(parents=True)
    out_dir = base / "out" / "batch-002"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(ar, "run_dir", lambda rid: tmp_path / "agentrun" / rid)

    (out_dir / "g-0000-001.jsonl").write_text(json.dumps(_decision("V:VIDA:p5:anonymous")) + "\n")
    items = [{"kind": "simgroup", "party": "V", "sys": "V.txt", "gf": "g-0000.json",
              "cids": ["V:VIDA:p5:anonymous"]}]
    # an older empty manifest + the latest one that actually has out files
    (base / "batches" / "batch-001.json").write_text(json.dumps({
        "run_id": "test-run", "out_dir": str(base / "out" / "batch-001"), "items": []}))
    (base / "batches" / "batch-002.json").write_text(json.dumps({
        "run_id": "test-run", "out_dir": str(out_dir), "items": items}))

    out = tmp_path / "merged.json"
    merge(run_id="test-run", out_path=str(out))
    payload = json.loads(out.read_text())
    assert [s["cid"] for s in payload["sims"]] == ["V:VIDA:p5:anonymous"]
