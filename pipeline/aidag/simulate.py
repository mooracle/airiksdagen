"""Batch API simulation: build, submit, poll, collect. Resumable via custom_id.

One batch per (party, chunk). The party corpus is byte-identical system blocks
across a party's requests, with a 1h cache_control breakpoint — intra-batch
prompt-cache reads cut input cost to ~0.1x on the corpus prefix.

custom_id = "{parti}:{votering_id}:{prompt_version}:{arm}" — globally unique,
so `collect` can dedupe and `simulate` can skip already-answered requests.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from aidag.config import (
    BATCH_CHUNK_SIZE,
    DEFAULT_MODEL,
    INTERIM_DIR,
    PARTY_CODES,
    PROCESSED_DIR,
    PROMPT_VERSION,
    RESULTS_DIR,
)
from aidag.models import Decision
from aidag.promptgen import DECISION_SCHEMA, build_system_blocks, render_user_message

BATCHES_LEDGER = RESULTS_DIR / "batches.json"
MAX_TOKENS = 4000


def load_ledger() -> list[dict]:
    return json.loads(BATCHES_LEDGER.read_text()) if BATCHES_LEDGER.exists() else []


def save_ledger(ledger: list[dict]) -> None:
    BATCHES_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    BATCHES_LEDGER.write_text(json.dumps(ledger, indent=1))


def results_path(run_id: str, party: str) -> Path:
    return RESULTS_DIR / "simulations" / run_id / f"{party}.jsonl"


def collected_ids(run_id: str, party: str) -> set[str]:
    path = results_path(run_id, party)
    if not path.exists():
        return set()
    return {
        f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"
        for d in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    }


def load_cases(pilot: bool) -> list[dict]:
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    if pilot:
        selection = json.loads((RESULTS_DIR / "pilot_selection.json").read_text())
        cases = cases.filter(pl.col("votering_id").is_in(selection["votering_ids"]))
    return cases.sort("datum", "votering_id").to_dicts()


def build_request(case: dict, party: str, model: str, arm: str) -> dict:
    return {
        "custom_id": f"{party}:{case['votering_id']}:{PROMPT_VERSION}:{arm}",
        "params": {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": "medium",
                "format": {"type": "json_schema", "schema": DECISION_SCHEMA},
            },
            "system": build_system_blocks(party, case["datum"]),
            "messages": [{"role": "user", "content": render_user_message(case, arm=arm)}],
        },
    }


def dry_run_report(cases: list[dict], parties: list[str], model: str, arm: str) -> None:
    import os

    out_dir = INTERIM_DIR / "requests"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()
    else:
        print("  (no ANTHROPIC_API_KEY — token counts are chars/4 estimates)")

    n_requests = len(cases) * len(parties)
    sample_case = cases[0]
    corpus_tokens = {}
    user_tokens = []
    for party in parties:
        req = build_request(sample_case, party, model, arm)
        (out_dir / f"sample-{party}.json").write_text(
            json.dumps(req, ensure_ascii=False, indent=1)
        )
        if client is not None:
            count = client.messages.count_tokens(
                model=model, system=req["params"]["system"], messages=req["params"]["messages"]
            )
            corpus_tokens[party] = count.input_tokens
        else:
            chars = sum(len(b["text"]) for b in req["params"]["system"]) + len(
                req["params"]["messages"][0]["content"]
            )
            corpus_tokens[party] = chars // 4
    for case in cases[:20]:
        user_tokens.append(len(render_user_message(case, arm=arm)) // 4)  # rough chars/4

    avg_prefix = sum(corpus_tokens.values()) / len(corpus_tokens)
    avg_user = sum(user_tokens) / len(user_tokens) if user_tokens else 2000
    # Cached prefix costs ~0.1x after the first request per party+chunk.
    eff_input_per_req = avg_prefix * 0.1 + avg_user
    batch_in, batch_out = _batch_prices(model)
    est = (
        n_requests * eff_input_per_req / 1e6 * batch_in
        + n_requests * 1500 / 1e6 * batch_out
    )
    print(f"dry run: {n_requests} requests ({len(cases)} cases x {len(parties)} parties)")
    print(f"  prefix tokens/party: { {p: t for p, t in corpus_tokens.items()} }")
    print(f"  est. cost with caching: ~${est:.0f} (model {model}, batch prices)")
    print(f"  sample requests written to {out_dir}")


def _batch_prices(model: str) -> tuple[float, float]:
    """Batch API = 50% of standard per-MTok prices."""
    if "opus" in model:
        return 2.50, 12.50
    if "sonnet" in model:
        return 1.50, 7.50
    return 0.50, 2.50  # haiku


def run(
    run_id: str,
    pilot: bool = False,
    party: str | None = None,
    model: str | None = None,
    arm: str = "anonymous",
    dry_run: bool = False,
) -> None:
    model = model or DEFAULT_MODEL
    parties = [party.upper()] if party else PARTY_CODES
    cases = load_cases(pilot)
    if dry_run:
        dry_run_report(cases, parties, model, arm)
        return

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    ledger = load_ledger()
    submitted_ids = {
        cid for entry in ledger if entry["run_id"] == run_id for cid in entry["custom_ids"]
    }

    for p in parties:
        done = collected_ids(run_id, p)
        requests = []
        for case in cases:
            req = build_request(case, p, model, arm)
            if req["custom_id"] in done or req["custom_id"] in submitted_ids:
                continue
            requests.append(req)
        if not requests:
            print(f"{p}: nothing to submit")
            continue
        for i in range(0, len(requests), BATCH_CHUNK_SIZE):
            chunk = requests[i : i + BATCH_CHUNK_SIZE]
            batch = client.messages.batches.create(
                requests=[
                    Request(
                        custom_id=r["custom_id"],
                        params=MessageCreateParamsNonStreaming(**r["params"]),
                    )
                    for r in chunk
                ]
            )
            ledger.append({
                "batch_id": batch.id,
                "run_id": run_id,
                "party": p,
                "model": model,
                "arm": arm,
                "n_requests": len(chunk),
                "custom_ids": [r["custom_id"] for r in chunk],
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "collected": False,
            })
            save_ledger(ledger)
            print(f"{p}: submitted batch {batch.id} ({len(chunk)} requests)")
    print("all batches submitted; run `aidag collect --run-id", run_id, "` to gather results")


def collect(run_id: str) -> None:
    import anthropic

    client = anthropic.Anthropic()
    ledger = load_ledger()
    open_batches = [e for e in ledger if e["run_id"] == run_id and not e["collected"]]
    if not open_batches:
        print("no open batches for this run")
        return

    if any(e["party"] == "_probe" for e in open_batches):
        from aidag import probe as probe_mod

        probe_mod.collect(run_id)
        ledger = load_ledger()
        open_batches = [
            e for e in ledger
            if e["run_id"] == run_id and not e["collected"] and e["party"] != "_probe"
        ]

    for entry in open_batches:
        batch = client.messages.batches.retrieve(entry["batch_id"])
        if batch.processing_status != "ended":
            counts = batch.request_counts
            print(f"{entry['batch_id']} [{entry['party']}]: {batch.processing_status} "
                  f"(processing={counts.processing}, succeeded={counts.succeeded})")
            continue

        path = results_path(run_id, entry["party"])
        path.parent.mkdir(parents=True, exist_ok=True)
        done = collected_ids(run_id, entry["party"])
        n_ok = n_err = 0
        with open(path, "a") as f:
            for result in client.messages.batches.results(entry["batch_id"]):
                if result.custom_id in done:
                    continue
                if result.result.type != "succeeded":
                    n_err += 1
                    print(f"  {result.custom_id}: {result.result.type}")
                    continue
                msg = result.result.message
                text = next((b.text for b in msg.content if b.type == "text"), "")
                parti, votering_id, prompt_version, arm = result.custom_id.split(":")
                try:
                    payload = json.loads(text)
                    decision = Decision(
                        votering_id=votering_id,
                        parti=parti,
                        run_id=run_id,
                        prompt_version=prompt_version,
                        model=entry["model"],
                        arm=arm,
                        usage={
                            "input_tokens": msg.usage.input_tokens,
                            "output_tokens": msg.usage.output_tokens,
                            "cache_read_input_tokens": msg.usage.cache_read_input_tokens or 0,
                        },
                        batch_id=entry["batch_id"],
                        collected_at=datetime.now(timezone.utc).isoformat(),
                        **payload,
                    )
                except Exception as e:  # noqa: BLE001 — quarantine malformed output
                    n_err += 1
                    print(f"  {result.custom_id}: invalid decision payload ({e})")
                    continue
                f.write(decision.model_dump_json() + "\n")
                n_ok += 1
        entry["collected"] = True
        save_ledger(ledger)
        print(f"{entry['batch_id']} [{entry['party']}]: collected {n_ok} decisions, {n_err} errors")


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def verify_run(run_id: str):
    """Checks for `aidag verify simulate --run-id X`. Yields (name, ok, detail)."""
    from aidag.corpus import documents_for

    sim_dir = RESULTS_DIR / "simulations" / run_id
    files = sorted(sim_dir.glob("*.jsonl")) if sim_dir.exists() else []
    yield ("results exist", bool(files), f"{len(files)} party files")
    _cases = pl.read_parquet(
        PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum", "rm"]
    )
    datum_by_vid = {r["votering_id"]: r["datum"] for r in _cases.iter_rows(named=True)}
    rm_by_vid = {r["votering_id"]: r["rm"] for r in _cases.iter_rows(named=True)}
    seen: set[str] = set()
    dupes = 0
    bad_quotes = 0
    out_of_context = 0
    n = 0
    corpus_map = {}
    for path in files:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            n += 1
            cid = f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"
            if cid in seen:
                dupes += 1
            seen.add(cid)
            for c in d.get("citations", []):
                # exactly what THIS decision's agent was served, from the same
                # function the prompt builder used — a citation to anything else
                # could only have come from the model's memory. The set is
                # per-(party, date, case) under p5: the programme and the shadow
                # budget roll over on their adoption dates, and a party voting on
                # its own budget never sees it.
                datum = datum_by_vid.get(d["votering_id"], "")
                rm = rm_by_vid.get(d["votering_id"], "")
                key = (d["parti"], datum, rm, d["votering_id"], d["prompt_version"])
                if key not in corpus_map:
                    # normalized: PDF extraction has arbitrary line wraps, and
                    # models legitimately collapse them when quoting
                    corpus_map[key] = {
                        kind: _normalize_ws(text)
                        for kind, _tag, text in documents_for(*key)
                    }
                in_context = corpus_map[key]
                doc = c["document"]
                if doc not in in_context:
                    out_of_context += 1
                source = in_context.get(doc, "")
                if c["quote"] and _normalize_ws(c["quote"]) not in source:
                    bad_quotes += 1
    yield ("no duplicate custom_ids", dupes == 0, f"{dupes} dupes in {n} decisions")
    yield ("citation quotes are real substrings", bad_quotes == 0, f"{bad_quotes} hallucinated of {n}")
    yield (
        "citations only cite in-context documents",
        out_of_context == 0,
        f"{out_of_context} citations to documents the agent never saw",
    )
