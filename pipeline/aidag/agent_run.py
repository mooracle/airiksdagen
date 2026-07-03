"""Checkpointed batch preparation for the subagent-based full run.

The full run = 2,539 cases x 8 parties (decisions) + 2,539 probes, executed as
Claude Code subagent batches (Opus orchestrator, Sonnet vote agents) rather
than the paid Batch API. See docs/orchestration-full-run.md for the protocol.

Checkpoint model — no separate state file:
  done      = custom_ids present in data/results/simulations/{run_id}/*.jsonl
              (+ votering_ids in probes/{run_id}/probe.jsonl)
  pending   = everything else
`prepare` always emits the next batch from pending, so re-running after a
partial/failed/limit-interrupted batch automatically resumes.

Layout under data/interim/agentrun/{run_id}/:
  system/{party}-{base|tido}.txt   party corpus + role (12 files, written once)
  cases/{vid}.json                 {"user": <case message>} (shared by parties)
  probes/{vid}.json                {"prompt": <probe prompt>}
  batches/batch-{NNN}.json         manifest for one workflow invocation
"""

from __future__ import annotations

import json

import polars as pl

from aidag.config import INTERIM_DIR, PARTY_CODES, PROCESSED_DIR, PROMPT_VERSION
from aidag.probe import PROBE_SYSTEM, collected_probe_ids, probe_user_message
from aidag.promptgen import build_system_blocks, render_user_message, tido_applies
from aidag.simulate import collected_ids

ARM = "anonymous"


def run_dir(run_id: str):
    return INTERIM_DIR / "agentrun" / run_id


def _load_cases() -> list[dict]:
    return (
        pl.read_parquet(PROCESSED_DIR / "cases.parquet")
        .sort("datum", "votering_id")
        .to_dicts()
    )


def _system_filename(party: str, datum: str) -> str:
    return f"{party}-{'tido' if tido_applies(party, datum) else 'base'}.txt"


def _pending(run_id: str, cases: list[dict], include_probes: bool, mirror_run: str | None = None):
    """Pending work. With mirror_run, the universe is restricted to the cids
    already COLLECTED in that run — used for methodology experiments that
    re-run the same decisions under a different execution design."""
    done = set()
    for party in PARTY_CODES:
        done |= collected_ids(run_id, party)
    universe = None
    if mirror_run:
        universe = set()
        for party in PARTY_CODES:
            universe |= collected_ids(mirror_run, party)
    sims = []
    for case in cases:
        for party in PARTY_CODES:
            cid = f"{party}:{case['votering_id']}:{PROMPT_VERSION}:{ARM}"
            if cid in done:
                continue
            if universe is not None and cid not in universe:
                continue
            sims.append((cid, party, case))
    probes = []
    if include_probes and mirror_run is None:
        probes_done = collected_probe_ids(run_id)
        probes = [c for c in cases if c["votering_id"] not in probes_done]
    return sims, probes


def prepare(
    run_id: str,
    batch_size: int = 240,
    include_probes: bool = True,
    group: int = 1,
    mirror_run: str | None = None,
) -> None:
    """Write the NEXT batch manifest from whatever is still pending.

    group > 1 packs same-party, same-month(+same-system-file) cases into
    'simgroup' manifest items — one agent decides several cases, reading the
    party corpus once (see scripts/grouped_batch_workflow.js)."""
    base = run_dir(run_id)
    cases = _load_cases()
    sims, probes = _pending(run_id, cases, include_probes, mirror_run=mirror_run)
    if not sims and not probes:
        print("nothing pending — run complete")
        return

    (base / "system").mkdir(parents=True, exist_ok=True)
    (base / "cases").mkdir(exist_ok=True)
    (base / "probes").mkdir(exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)

    # party role+corpus files (idempotent; 12 variants)
    for party in PARTY_CODES:
        for datum in ("2022-09-01", "2023-01-01"):  # pre-/post-Tidö
            name = _system_filename(party, datum)
            path = base / "system" / name
            if not path.exists():
                text = "\n\n".join(b["text"] for b in build_system_blocks(party, datum))
                path.write_text(text)

    # take the next slice; probes (cheap) fill whatever room decisions leave,
    # so probe batches naturally run once all decisions are collected
    batch_sims = sims[:batch_size]
    room = max(0, batch_size - len(batch_sims))
    batch_probes = probes[:room]

    def _case_file(case: dict):
        path = base / "cases" / f"{case['votering_id']}.json"
        if not path.exists():
            path.write_text(
                json.dumps({"user": render_user_message(case, arm=ARM)}, ensure_ascii=False)
            )
        return path

    items = []
    if group > 1:
        # pack same-party cases (same Tidö-era system file, same month, in
        # chronological order) into groups of <= `group` for one agent each
        buckets: dict[tuple, list] = {}
        for cid, party, case in batch_sims:
            key = (party, _system_filename(party, case["datum"]), case["datum"][:7])
            buckets.setdefault(key, []).append((cid, case))
        # compact on purpose: party/vid/case_file derive from the cid + the
        # manifest-level dirs, so the workflow's loader agent can transcribe
        # a large manifest within its output-token limit
        for (party, system_name, _month), members in sorted(buckets.items()):
            for i in range(0, len(members), group):
                chunk = members[i : i + group]
                for _cid, case in chunk:
                    _case_file(case)
                items.append({
                    "kind": "simgroup",
                    "party": party,
                    "sys": system_name,
                    "cids": [cid for cid, _case in chunk],
                })
    else:
        for cid, party, case in batch_sims:
            _case_file(case)
            items.append({
                "kind": "sim",
                "cid": cid,
                "sys": _system_filename(party, case["datum"]),
            })
    for case in batch_probes:
        probe_file = base / "probes" / f"{case['votering_id']}.json"
        if not probe_file.exists():
            probe_file.write_text(
                json.dumps(
                    {"prompt": PROBE_SYSTEM + "\n\n" + probe_user_message(case)},
                    ensure_ascii=False,
                )
            )
        items.append({"kind": "probe", "vid": case["votering_id"]})

    existing = sorted((base / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1
    manifest_path = base / "batches" / f"batch-{n:03d}.json"
    n_sims = sum(
        len(i["cids"]) if i["kind"] == "simgroup" else 1
        for i in items
        if i["kind"] in ("sim", "simgroup")
    )
    n_probes = sum(1 for i in items if i["kind"] == "probe")
    # n_sims/n_probes let the workflow script verify the manifest survived the
    # loader agent's transcription intact (it re-counts and compares)
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "n_sims": n_sims,
                "n_probes": n_probes,
                "cases_dir": str(base / "cases"),
                "probes_dir": str(base / "probes"),
                "system_dir": str(base / "system"),
                "items": items,
            },
            ensure_ascii=False,
        )
    )
    print(f"batch manifest: {manifest_path}")
    print(f"  {n_sims} decisions + {n_probes} probes in this batch")
    print(f"  remaining after this batch: {len(sims) - n_sims} decisions, {len(probes) - n_probes} probes")


def status(run_id: str) -> None:
    cases = _load_cases()
    sims, probes = _pending(run_id, cases, include_probes=True)
    total_sims = len(cases) * len(PARTY_CODES)
    done_sims = total_sims - len(sims)
    done_probes = len(cases) - len(probes)
    print(f"run {run_id}:")
    print(f"  decisions: {done_sims}/{total_sims} done ({done_sims / total_sims:.1%}), {len(sims)} pending")
    print(f"  probes:    {done_probes}/{len(cases)} done, {len(probes)} pending")
    if sims:
        # temporal frontier: the batches walk chronologically
        print(f"  next pending case date: {sims[0][2]['datum']}")
    batches = sorted((run_dir(run_id) / "batches").glob("batch-*.json"))
    print(f"  batch manifests issued: {len(batches)}")
