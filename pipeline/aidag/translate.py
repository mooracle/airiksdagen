"""English translations of case texts and AI decisions, as pipeline data.

The English site must give readers the full information, not just translated
UI labels — so the Swedish case texts (title, committee proposal, decision
notice, counter-proposals) and every AI decision (motivering, citation quotes,
princip labels, worldstate factors) get English translations produced by the
same checkpointed Claude Code subagent workflow as the simulation run
(scripts/translate_batch_workflow.js) — no API key needed.

Storage (append-only JSONL, committed like simulation results):
  data/results/translations/cases.jsonl              run-INDEPENDENT case texts
  data/results/translations/{run_id}/decisions.jsonl per-run decision texts

Checkpoint model mirrors agent_run.py: done = ids present in the JSONL files,
pending = everything else, `translate-prepare` always emits the next batch.
Run `translate-prepare` only AFTER `repair-citations` — quote translations
must be made from the repaired (verbatim) Swedish quotes.

Every unit's source text travels INSIDE the request file, so translation
agents read exactly one file and need no other context.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import polars as pl

from aidag.config import INTERIM_DIR, PROCESSED_DIR, RESULTS_DIR

TRANSLATIONS_DIR = RESULTS_DIR / "translations"
CASES_PER_REQUEST = 6  # units bundled into one translation agent's file
DECISIONS_PER_REQUEST = 12

INSTRUCTIONS = (
    "Translate every Swedish text in `units` to natural, precise English for a "
    "public research site about Riksdag votes. Rules: translate faithfully — no "
    "summarizing, no commentary, no added facts; keep numbers, party names and "
    "codes exactly; keep established Swedish terms with a short English gloss on "
    "first use in a unit where helpful (e.g. 'tillkännagivande (formal request)', "
    "'Tidöavtalet (the Tidö agreement)'); citation quotes are verbatim evidence — "
    "translate them accurately and completely; keep every output array exactly "
    "parallel to its input array (same length, same order)."
)


def run_dir(run_id: str):
    return INTERIM_DIR / "translate" / run_id


def cases_path():
    return TRANSLATIONS_DIR / "cases.jsonl"


def decisions_path(run_id: str):
    return TRANSLATIONS_DIR / run_id / "decisions.jsonl"


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_case_translations() -> dict[str, dict]:
    return {r["votering_id"]: r for r in _read_jsonl(cases_path())}


def load_decision_translations(run_id: str) -> dict[str, dict]:
    return {r["cid"]: r for r in _read_jsonl(decisions_path(run_id))}


def _load_decisions(run_id: str) -> dict[str, dict]:
    """All anonymous-arm decisions of the run, keyed by cid."""
    out: dict[str, dict] = {}
    sim_dir = RESULTS_DIR / "simulations" / run_id
    if not sim_dir.exists():
        return out
    for path in sorted(sim_dir.glob("*.jsonl")):
        for d in _read_jsonl(path):
            if d.get("arm", "anonymous") != "anonymous":
                continue
            cid = f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"
            out[cid] = d
    return out


def _case_unit(case: dict) -> dict:
    alternatives = case["alternatives"]
    if isinstance(alternatives, str):
        alternatives = json.loads(alternatives)
    return {
        "votering_id": case["votering_id"],
        "rubrik": case["rubrik"],
        "dok_titel": case["dok_titel"],
        "forslag_text": case["forslag_text"],
        "notis": case["notis"],
        "alternatives": [a["text"] for a in alternatives],
    }


def _decision_unit(cid: str, d: dict) -> dict:
    return {
        "cid": cid,
        "motivering": d["motivering"],
        "citations": [
            {"quote": c["quote"], "princip": c.get("princip", "")} for c in d.get("citations", [])
        ],
        "omvarld": [
            {"faktor": f["faktor"], "effekt": f["effekt"]}
            for f in (d.get("omvarld") or {}).get("faktorer", [])
        ],
    }


def validate_case_translation(rec: dict, unit: dict) -> None:
    """Raise if a case translation does not align with its source unit."""
    for field in ("rubrik", "forslag_text"):
        if not str(rec.get(field, "")).strip():
            raise ValueError(f"empty {field}")
    if len(rec.get("alternatives", [])) != len(unit["alternatives"]):
        raise ValueError(
            f"alternatives length {len(rec.get('alternatives', []))} != {len(unit['alternatives'])}"
        )
    if bool(unit["notis"]) and not str(rec.get("notis", "")).strip():
        raise ValueError("empty notis")


def validate_decision_translation(rec: dict, unit: dict) -> None:
    """Raise if a decision translation does not align with its source unit."""
    if not str(rec.get("motivering", "")).strip():
        raise ValueError("empty motivering")
    if len(rec.get("citations", [])) != len(unit["citations"]):
        raise ValueError(
            f"citations length {len(rec.get('citations', []))} != {len(unit['citations'])}"
        )
    if len(rec.get("omvarld", [])) != len(unit["omvarld"]):
        raise ValueError(f"omvarld length {len(rec.get('omvarld', []))} != {len(unit['omvarld'])}")
    for c in rec.get("citations", []):
        if not str(c.get("quote", "")).strip():
            raise ValueError("empty citation quote")


def _pack(units: list[dict], per_request: int) -> list[list[dict]]:
    return [units[i : i + per_request] for i in range(0, len(units), per_request)]


def _pending(run_id: str) -> tuple[list[dict], list[dict]]:
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").sort("datum", "votering_id")
    done_cases = set(load_case_translations())
    pending_cases = [
        _case_unit(c) for c in cases.iter_rows(named=True) if c["votering_id"] not in done_cases
    ]
    decisions = _load_decisions(run_id)
    done_decisions = set(load_decision_translations(run_id))
    pending_decisions = [
        _decision_unit(cid, d) for cid, d in sorted(decisions.items()) if cid not in done_decisions
    ]
    return pending_cases, pending_decisions


def prepare(run_id: str, batch_size: int = 240) -> None:
    """Write the NEXT translation batch manifest from whatever is pending.

    One manifest item = one request file = one translation agent handling
    several units. Case texts (run-independent) are translated before the
    run's decision texts.
    """
    pending_cases, pending_decisions = _pending(run_id)
    if not pending_cases and not pending_decisions:
        print("nothing pending — translations complete")
        return

    base = run_dir(run_id)
    (base / "reqs").mkdir(parents=True, exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)

    groups = [("cases", g) for g in _pack(pending_cases, CASES_PER_REQUEST)]
    groups += [("decisions", g) for g in _pack(pending_decisions, DECISIONS_PER_REQUEST)]
    batch_groups = groups[:batch_size]

    existing = sorted((base / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1
    items = []
    for i, (kind, units) in enumerate(batch_groups):
        req_path = base / "reqs" / f"batch-{n:03d}-{kind}-{i:04d}.json"
        req_path.write_text(
            json.dumps(
                {"kind": kind, "instructions": INSTRUCTIONS, "units": units},
                ensure_ascii=False,
            )
        )
        items.append({"kind": kind, "path": str(req_path), "n_units": len(units)})

    manifest_path = base / "batches" / f"batch-{n:03d}.json"
    manifest_path.write_text(
        json.dumps({"run_id": run_id, "n_items": len(items), "items": items}, ensure_ascii=False)
    )
    n_case_units = sum(i["n_units"] for i in items if i["kind"] == "cases")
    n_dec_units = sum(i["n_units"] for i in items if i["kind"] == "decisions")
    print(f"translation batch manifest: {manifest_path}")
    print(f"  {len(items)} agents: {n_case_units} case units + {n_dec_units} decision units")
    print(
        f"  remaining after this batch: {len(pending_cases) - n_case_units} cases, "
        f"{len(pending_decisions) - n_dec_units} decisions"
    )


def ingest(run_id: str, input_path: str, model: str) -> None:
    """Ingest a translate workflow result ({cases: [...], decisions: [...]}).

    Idempotent: dedupes on votering_id / cid; every record is validated for
    alignment against its Swedish source before it is written.
    """
    data = json.loads(open(input_path).read())
    now = datetime.now(timezone.utc).isoformat()

    cases_by_vid = {
        c["votering_id"]: _case_unit(c)
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
    }
    n_cases = n_decisions = n_bad = 0

    done_cases = set(load_case_translations())
    cases_path().parent.mkdir(parents=True, exist_ok=True)
    with open(cases_path(), "a") as f:
        for rec in data.get("cases", []):
            vid = rec.get("votering_id")
            if vid in done_cases:
                continue
            try:
                unit = cases_by_vid[vid]
                validate_case_translation(rec, unit)
            except Exception as e:  # noqa: BLE001
                n_bad += 1
                print(f"  skipped case {vid}: {e}")
                continue
            done_cases.add(vid)
            row = {k: rec[k] for k in ("votering_id", "rubrik", "dok_titel", "forslag_text", "notis", "alternatives")}
            row |= {"model": model, "collected_at": now}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n_cases += 1

    decisions = _load_decisions(run_id)
    done_decisions = set(load_decision_translations(run_id))
    dpath = decisions_path(run_id)
    dpath.parent.mkdir(parents=True, exist_ok=True)
    with open(dpath, "a") as f:
        for rec in data.get("decisions", []):
            cid = rec.get("cid")
            if cid in done_decisions:
                continue
            try:
                if cid not in decisions:
                    raise ValueError("cid not in run results")
                validate_decision_translation(rec, _decision_unit(cid, decisions[cid]))
            except Exception as e:  # noqa: BLE001
                n_bad += 1
                print(f"  skipped decision {cid}: {e}")
                continue
            done_decisions.add(cid)
            row = {k: rec[k] for k in ("cid", "motivering", "citations", "omvarld")}
            row |= {"model": model, "collected_at": now}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n_decisions += 1

    print(f"ingested {n_cases} case + {n_decisions} decision translations ({n_bad} skipped)")


def status(run_id: str) -> None:
    pending_cases, pending_decisions = _pending(run_id)
    n_cases_total = pl.read_parquet(PROCESSED_DIR / "cases.parquet").height
    n_dec_total = len(_load_decisions(run_id))
    print(f"translations (run {run_id}):")
    print(f"  cases:     {n_cases_total - len(pending_cases)}/{n_cases_total} done, {len(pending_cases)} pending")
    print(f"  decisions: {n_dec_total - len(pending_decisions)}/{n_dec_total} done, {len(pending_decisions)} pending")


def verify_translations(run_id: str | None):
    """Checks for `aidag verify translate`. Yields (name, ok, detail)."""
    cases_by_vid = {
        c["votering_id"]: _case_unit(c)
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
    }
    bad = 0
    trs = load_case_translations()
    for vid, rec in trs.items():
        try:
            validate_case_translation(rec, cases_by_vid[vid])
        except Exception:  # noqa: BLE001
            bad += 1
    yield (
        "case translations align with sources",
        bad == 0,
        f"{len(trs)}/{len(cases_by_vid)} translated, {bad} misaligned",
    )
    if run_id:
        decisions = _load_decisions(run_id)
        dtrs = load_decision_translations(run_id)
        bad = 0
        for cid, rec in dtrs.items():
            try:
                if cid not in decisions:
                    raise ValueError
                validate_decision_translation(rec, _decision_unit(cid, decisions[cid]))
            except Exception:  # noqa: BLE001
                bad += 1
        yield (
            "decision translations align with results",
            bad == 0,
            f"{len(dtrs)}/{len(decisions)} translated, {bad} misaligned",
        )
