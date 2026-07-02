"""Ingest a subagent-based simulation run (Claude Code workflow) into the
standard results layout, so aggregate/verify/export-site work unchanged.

Usage: uv run python -m aidag.ingest_agent_run --run-id agent-pilot-v1 --input result.json --model <model>

Input format: {"sims": [{cid, party, vid, decision{...}}], "probes": [{vid, result{...}}]}
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import polars as pl

from aidag.config import PROCESSED_DIR, RESULTS_DIR
from aidag.models import Decision, Probe
from aidag.probe import probe_results_path
from aidag.simulate import results_path


def run(run_id: str, input_path: str, model: str) -> None:
    data = json.loads(open(input_path).read())
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual: dict[str, dict[str, str]] = {}
    for r in positions.iter_rows(named=True):
        actual.setdefault(r["votering_id"], {})[r["parti"]] = r["position"]

    by_party: dict[str, list[Decision]] = {}
    n_bad = 0
    for sim in data["sims"]:
        try:
            parti, vid, prompt_version, arm = sim["cid"].split(":")
            decision = Decision(
                votering_id=vid,
                parti=parti,
                run_id=run_id,
                prompt_version=prompt_version,
                model=model,
                arm=arm,
                batch_id="claude-code-workflow",
                collected_at=datetime.now(timezone.utc).isoformat(),
                **sim["decision"],
            )
        except Exception as e:  # noqa: BLE001
            n_bad += 1
            print(f"  skipped {sim.get('cid')}: {e}")
            continue
        by_party.setdefault(parti, []).append(decision)

    n = 0
    for parti, decisions in sorted(by_party.items()):
        path = results_path(run_id, parti)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if path.exists():
            existing = {json.loads(l)["votering_id"] for l in path.read_text().splitlines() if l.strip()}
        with open(path, "a") as f:
            for d in decisions:
                if d.votering_id in existing:
                    continue
                f.write(d.model_dump_json() + "\n")
                n += 1

    n_probes = 0
    ppath = probe_results_path(run_id)
    ppath.parent.mkdir(parents=True, exist_ok=True)
    existing_probes = set()
    if ppath.exists():
        existing_probes = {json.loads(l)["votering_id"] for l in ppath.read_text().splitlines() if l.strip()}
    with open(ppath, "a") as f:
        for p in data.get("probes", []):
            vid = p["vid"]
            if vid in existing_probes:
                continue
            predicted = p["result"].get("positions", {})
            act = actual.get(vid, {})
            matches = sum(1 for parti, v in predicted.items() if v != "okänt" and act.get(parti) == v)
            probe = Probe(
                votering_id=vid,
                run_id=run_id,
                model=model,
                predicted_positions=predicted,
                actual_positions=act,
                exact_match_count=matches,
                recalls_case=bool(p["result"].get("recalls_case")),
                raw_answer=p["result"].get("notes", ""),
                batch_id="claude-code-workflow",
            )
            f.write(probe.model_dump_json() + "\n")
            n_probes += 1

    print(f"ingested {n} decisions ({n_bad} skipped), {n_probes} probes -> run_id={run_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", default="claude-fable-5[1m] (claude-code-subagent)")
    args = parser.parse_args()
    run(run_id=args.run_id, input_path=args.input, model=args.model)
