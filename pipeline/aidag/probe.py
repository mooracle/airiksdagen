"""Memorization probe: measure training-data contamination per case.

The inverse of the simulation prompt: NO party corpus, NO knowledge base,
identifiers INCLUDED — we ask the model cold how each party actually voted.
High recall on a case means agreement scores there conflate memorization with
document-following; the correlation is published, not hidden.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import polars as pl

from aidag.config import (
    BATCH_CHUNK_SIZE,
    DEFAULT_MODEL,
    PARTY_CODES,
    PROCESSED_DIR,
    PROMPT_VERSION,
    RESULTS_DIR,
)
from aidag.models import Probe
from aidag.simulate import load_cases, load_ledger, save_ledger

PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "recalls_case": {"type": "boolean"},
        "positions": {
            "type": "object",
            "properties": {
                p: {"type": "string", "enum": ["Ja", "Nej", "Avstår", "Frånvarande", "okänt"]}
                for p in PARTY_CODES
            },
            "required": PARTY_CODES,
            "additionalProperties": False,
        },
        "notes": {"type": "string"},
    },
    "required": ["recalls_case", "positions", "notes"],
    "additionalProperties": False,
}

PROBE_SYSTEM = """\
Du deltar i en kontamineringsmätning för ett forskningsprojekt. Frågan gäller en \
faktisk votering i Sveriges riksdag. Svara utifrån vad du faktiskt minns från din \
träningsdata om just denna votering. Gissa inte utifrån allmän partilogik: om du inte \
minns voteringen, sätt recalls_case=false och "okänt" för partier du inte minns."""


def probe_user_message(case: dict) -> str:
    return (
        f"Votering i Sveriges riksdag, betänkande {case['rm']}:{case['beteckning']} "
        f"punkt {case['punkt']} ({case['dok_id']}), beslutad {case['datum']}.\n"
        f"Rubrik: {case['rubrik']}\n"
        f"Förslag: {case['forslag_text'][:1500]}\n\n"
        f"Hur röstade respektive parti (S, M, SD, C, V, KD, MP, L) i sakfrågan?"
    )


def probe_results_path(run_id: str):
    return RESULTS_DIR / "probes" / run_id / "probe.jsonl"


def collected_probe_ids(run_id: str) -> set[str]:
    path = probe_results_path(run_id)
    if not path.exists():
        return set()
    return {json.loads(l)["votering_id"] for l in path.read_text().splitlines() if l.strip()}


def run(run_id: str, pilot: bool = False, model: str | None = None, dry_run: bool = False) -> None:
    model = model or DEFAULT_MODEL
    cases = load_cases(pilot)
    done = collected_probe_ids(run_id)
    todo = [c for c in cases if c["votering_id"] not in done]
    print(f"probe: {len(todo)} cases to probe")
    if dry_run:
        if todo:
            print(probe_user_message(todo[0]))
        return

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    ledger = load_ledger()
    submitted = {
        cid for e in ledger if e["run_id"] == run_id and e["party"] == "_probe"
        for cid in e["custom_ids"]
    }
    todo = [c for c in todo if f"probe:{c['votering_id']}" not in submitted]

    for i in range(0, len(todo), BATCH_CHUNK_SIZE):
        chunk = todo[i : i + BATCH_CHUNK_SIZE]
        batch = client.messages.batches.create(
            requests=[
                Request(
                    custom_id=f"probe:{c['votering_id']}",
                    params=MessageCreateParamsNonStreaming(
                        model=model,
                        max_tokens=1500,
                        output_config={"format": {"type": "json_schema", "schema": PROBE_SCHEMA}},
                        system=PROBE_SYSTEM,
                        messages=[{"role": "user", "content": probe_user_message(c)}],
                    ),
                )
                for c in chunk
            ]
        )
        ledger.append({
            "batch_id": batch.id,
            "run_id": run_id,
            "party": "_probe",
            "model": model,
            "arm": "probe",
            "n_requests": len(chunk),
            "custom_ids": [f"probe:{c['votering_id']}" for c in chunk],
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "collected": False,
        })
        save_ledger(ledger)
        print(f"probe: submitted batch {batch.id} ({len(chunk)} requests)")
    print(f"run `aidag collect-probe` via collect --run-id {run_id} when batches end")


def collect(run_id: str) -> None:
    """Collect probe batches; called from `aidag collect` for _probe entries."""
    import anthropic

    client = anthropic.Anthropic()
    ledger = load_ledger()
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual = {
        vid: dict(zip(g["parti"], g["position"]))
        for vid, g in positions.group_by("votering_id")
        .agg(pl.col("parti"), pl.col("position"))
        .rows_by_key("votering_id", named=True).items()
    }

    for entry in [e for e in ledger if e["run_id"] == run_id and e["party"] == "_probe" and not e["collected"]]:
        batch = client.messages.batches.retrieve(entry["batch_id"])
        if batch.processing_status != "ended":
            print(f"{entry['batch_id']} [probe]: {batch.processing_status}")
            continue
        path = probe_results_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        done = collected_probe_ids(run_id)
        n = 0
        with open(path, "a") as f:
            for result in client.messages.batches.results(entry["batch_id"]):
                vid = result.custom_id.split(":", 1)[1]
                if vid in done or result.result.type != "succeeded":
                    continue
                msg = result.result.message
                text = next((b.text for b in msg.content if b.type == "text"), "")
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                act = actual.get(vid, {})
                predicted = payload.get("positions", {})
                matches = sum(
                    1 for p, v in predicted.items() if v != "okänt" and act.get(p) == v
                )
                probe = Probe(
                    votering_id=vid,
                    run_id=run_id,
                    model=entry["model"],
                    predicted_positions=predicted,
                    actual_positions=act,
                    exact_match_count=matches,
                    recalls_case=bool(payload.get("recalls_case")),
                    raw_answer=payload.get("notes", ""),
                    batch_id=entry["batch_id"],
                )
                f.write(probe.model_dump_json() + "\n")
                n += 1
        entry["collected"] = True
        save_ledger(ledger)
        print(f"{entry['batch_id']} [probe]: collected {n}")
