"""Ingest a subagent-based simulation run (Claude Code workflow) into the
standard results layout, so aggregate/verify/export-site work unchanged.

Usage: uv run python -m aidag.ingest_agent_run --run-id agent-pilot-v1 --input result.json --model <model>

Input format: {"sims": [{cid, party, vid, decision{...}}]}
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import polars as pl

from aidag.config import PARTY_CODES, PROCESSED_DIR, RESULTS_DIR
from aidag.models import Decision
from aidag.promptgen import derive_rost
from aidag.simulate import collected_ids, results_path


DEFAULT_BATCH_ID = "claude-code-workflow"


def parse_sim(
    sim: dict,
    run_id: str,
    model: str,
    known_vids: set[str],
    batch_id: str = DEFAULT_BATCH_ID,
) -> Decision:
    """Validate one workflow sim result against the case universe.

    The cid travels through an agent transcription (workflow loader), so a
    corrupted id must be rejected here — a bogus votering_id written to the
    results would never match a case, while the real id stayed pending forever.
    """
    parti, vid, prompt_version, arm = sim["cid"].split(":")
    if parti not in PARTY_CODES:
        raise ValueError(f"unknown party {parti!r}")
    if vid not in known_vids:
        raise ValueError(f"votering_id {vid!r} not in cases")
    if sim.get("party") not in (None, parti) or sim.get("vid") not in (None, vid):
        raise ValueError(f"cid disagrees with item fields {sim.get('party')}/{sim.get('vid')}")
    decision = dict(sim["decision"])
    # p6 agents decide a STANCE (`hallning`); the vote is derived here in code and
    # never predicted by the model — that separation is the whole point of the
    # policy-first rebuild. Storing the derived `rost` alongside it keeps every
    # downstream reader (aggregate, export_site, compare_runs) working unchanged,
    # while the gap — derived vote vs the real one — stays computable at analysis
    # time. p4/p5 decisions carry `rost` directly and are passed through untouched.
    if "rost" not in decision and "hallning" in decision:
        decision["rost"] = derive_rost(decision["hallning"])
    return Decision(
        votering_id=vid,
        parti=parti,
        run_id=run_id,
        prompt_version=prompt_version,
        model=model,
        arm=arm,
        batch_id=batch_id,
        collected_at=datetime.now(timezone.utc).isoformat(),
        **decision,
    )


def run(run_id: str, input_path: str, model: str, batch_id: str = DEFAULT_BATCH_ID) -> None:
    data = json.loads(open(input_path).read())
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual: dict[str, dict[str, str]] = {}
    for r in positions.iter_rows(named=True):
        actual.setdefault(r["votering_id"], {})[r["parti"]] = r["position"]
    known_vids = set(actual)

    by_party: dict[str, list[Decision]] = {}
    n_bad = 0
    for sim in data["sims"]:
        try:
            decision = parse_sim(sim, run_id, model, known_vids, batch_id=batch_id)
        except Exception as e:  # noqa: BLE001
            n_bad += 1
            print(f"  skipped {sim.get('cid')}: {e}")
            continue
        by_party.setdefault(decision.parti, []).append(decision)

    n = 0
    for parti, decisions in sorted(by_party.items()):
        path = results_path(run_id, parti)
        path.parent.mkdir(parents=True, exist_ok=True)
        # dedupe on the full custom_id, matching agent-prepare's pending logic —
        # a votering_id-only key would silently drop a later labeled/new-prompt
        # arm while prepare kept re-issuing it forever
        existing = collected_ids(run_id, parti)
        with open(path, "a") as f:
            for d in decisions:
                cid = f"{d.parti}:{d.votering_id}:{d.prompt_version}:{d.arm}"
                if cid in existing:
                    continue
                existing.add(cid)
                f.write(d.model_dump_json() + "\n")
                n += 1

    print(f"ingested {n} decisions ({n_bad} skipped) -> run_id={run_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", default="claude-fable-5[1m] (claude-code-subagent)")
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID)
    args = parser.parse_args()
    run(run_id=args.run_id, input_path=args.input, model=args.model, batch_id=args.batch_id)
