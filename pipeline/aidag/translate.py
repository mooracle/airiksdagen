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
import re
from datetime import datetime, timezone

import polars as pl

from aidag.config import INTERIM_DIR, PROCESSED_DIR, RESULTS_DIR

TRANSLATIONS_DIR = RESULTS_DIR / "translations"
CASES_PER_REQUEST = 6  # units bundled into one translation agent's file
# 40, not 12: the preamble below is re-sent with every request, so larger groups
# amortize it — 5,400 decisions go from 450 requests to 135, and the repeated
# preamble from ~0.68 MTok to ~0.20 MTok. Past ~40 the saving flattens while the
# cost of a lost agent keeps rising (a death re-issues the whole group), and the
# structured output approaches 10k tokens.
#
# Deliberately NOT solved by giving one agent many groups in sequence: the agent's
# own prior source and output accumulate in its context and are re-read on every
# later turn. Even at the 0.1x cache-read rate that overhead passes the ~1.5k-token
# preamble it saves within a few turns, and grows from there — measured ~12% more
# expensive at 100 units/agent, ~27% at 200.
DECISIONS_PER_REQUEST = 40

# Locked renderings for the project's own analytical vocabulary. These are not
# parliamentary procedure — procedure barely occurs in agent text (`utskottet`
# appears in 31 of 2,539 cases) — they are the handful of terms the agents use
# constantly (`planen` 7.5k occurrences, `motförslaget` 2.2k, `uttryckligen` 1k).
# Each request is translated by a different agent with no shared context, so
# without this the same term renders differently from one page to the next; the
# right-hand column is what the site's own English UI already says, so the prose
# and the labels around it have to agree.
GLOSSARY = (
    "Terminology — use exactly these renderings, they match the site's English UI:\n"
    "- planen / planens -> the plan / the plan's (the party's own manifesto + "
    "programme, read as one document; never 'the policy', 'the document', 'the platform')\n"
    "- motförslaget / motförslagets -> the counter-proposal / the counter-proposal's "
    "(the reservation put against the committee proposal; never 'the counter-motion' "
    "or 'the alternative proposal')\n"
    "- partiprogram(met) -> party programme (British spelling, not 'program')\n"
    "- principprogram(met) -> party programme (the same rendering, not 'principle "
    "programme' or 'programme of principles')\n"
    "- idéprogram(met) -> party programme (the same rendering — NOT 'idea programme', "
    "'ideas programme', 'programme of ideas' or 'policy programme'). This holds even "
    "though the party's own document is titled Idéprogram: 'Idéprogrammet slår fast' "
    "-> 'The party programme establishes'\n"
    "- valmanifest(et) -> election manifesto\n"
    "- valplattform(en) -> election manifesto (the same rendering, not 'election "
    "platform' or 'electoral platform')\n"
    "  Those five document names collapse into two English ones on purpose: the site "
    "labels every foundational document 'party programme' and every election document "
    "with the manifesto label, so prose that invents a third name contradicts the link "
    "printed next to it.\n"
    "- Tidöavtalet -> the Tidö Agreement\n"
    "- uttryckligen -> explicitly; ett uttryckligt åtagande -> an explicit commitment\n"
    "- åtagande(n) -> commitment(s). Never 'undertaking' or 'obligation' — this holds "
    "for the noun in every position, including 'the plan's closest/bearing/binding "
    "åtagande' and 'these åtaganden'. (An unrelated skyldighet/förpliktelse — a "
    "reduction obligation, a reporting obligation — is still an obligation.)\n"
    "- ståndpunkt -> stance;  planen stödjer/avvisar -> the plan supports/rejects\n"
    "- reservation -> reservation (a formal counter-proposal in a committee report; "
    "keep the Swedish-derived term, do not translate as 'reservation' in the sense "
    "of a doubt)\n"
    "- betänkande -> committee report;  utskottet -> the committee;  "
    "riksdagen -> the Riksdag;  kammaren -> the chamber\n"
    "- yrkande -> motion point;  motion -> motion;  proposition -> government bill\n"
    "- tillkännagivande -> formal request to the government\n"
    "- avslå / bifalla -> reject / approve\n"
)

INSTRUCTIONS = (
    "Translate every Swedish text in `units` to natural, precise English for a "
    "public research site about Riksdag votes. Rules: translate faithfully — no "
    "summarizing, no commentary, no added facts; keep numbers, party names and "
    "codes exactly; citation quotes are verbatim evidence — "
    "translate them accurately and completely; keep every output array exactly "
    "parallel to its input array (same length, same order).\n\n"
    + GLOSSARY
    + "\nFor any other established Swedish term not listed above, keep the Swedish "
    "and add a short English gloss on first use in a unit, e.g. "
    "'utskottsinitiativ (a committee's own initiative)'."
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
    # a blanked (unverifiable) source quote has nothing to translate — only
    # require a translated quote where the source quote is itself non-empty
    for i, c in enumerate(rec.get("citations", [])):
        src_quote = unit["citations"][i]["quote"] if i < len(unit["citations"]) else ""
        if str(src_quote).strip() and not str(c.get("quote", "")).strip():
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


def _staged_ids(run_id: str) -> set[str]:
    """Ids already written into an earlier manifest's request files.

    Pending is normally derived from what is *ingested*, so calling prepare
    twice before ingesting re-issues the same units. That is exactly right for
    recovery (a dead agent's units are still pending and must be re-issued) and
    exactly wrong when staging the whole corpus up front as several manifests.
    `prepare(skip_staged=True)` subtracts these.
    """
    ids: set[str] = set()
    for req in sorted((run_dir(run_id) / "reqs").glob("*.json")):
        for unit in json.loads(req.read_text())["units"]:
            ids.add(unit.get("cid") or unit["votering_id"])
    return ids


def prepare(
    run_id: str,
    batch_size: int = 240,
    kind: str = "all",
    decided_only: bool = False,
    skip_staged: bool = False,
) -> None:
    """Write the NEXT translation batch manifest from whatever is pending.

    One manifest item = one request file = one translation agent handling
    several units. Case texts (run-independent) are packed before the run's
    decision texts; `kind` restricts the batch to one of the two.

    `decided_only` scopes pending CASE texts to voteringar that already have a
    decision in this run — so incremental translation keeps pace with the
    simulation instead of translating case texts for cases not yet simulated
    (decisions are per-run and already scoped, so they are unaffected).

    `skip_staged` also subtracts units an earlier manifest already covers, so a
    corpus larger than `batch_size` groups can be staged as consecutive
    manifests without ingesting in between. Leave it off when re-preparing
    after a run — dead agents' units live in a manifest that was already issued
    and have to be re-issued.
    """
    if kind not in ("all", "cases", "decisions"):
        raise ValueError(f"kind must be all|cases|decisions, got {kind!r}")
    pending_cases, pending_decisions = _pending(run_id)
    if decided_only:
        decided_vids = {d["votering_id"] for d in _load_decisions(run_id).values()}
        pending_cases = [c for c in pending_cases if c["votering_id"] in decided_vids]
    if skip_staged:
        staged = _staged_ids(run_id)
        pending_cases = [c for c in pending_cases if c["votering_id"] not in staged]
        pending_decisions = [d for d in pending_decisions if d["cid"] not in staged]

    groups = []
    if kind in ("all", "cases"):
        groups += [("cases", g) for g in _pack(pending_cases, CASES_PER_REQUEST)]
    if kind in ("all", "decisions"):
        groups += [("decisions", g) for g in _pack(pending_decisions, DECISIONS_PER_REQUEST)]
    if not groups:
        print("nothing pending — translations complete")
        return

    base = run_dir(run_id)
    (base / "reqs").mkdir(parents=True, exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)

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


def _flatten_chunks(data: dict) -> dict:
    """Accept both workflow result shapes: flat lists and `*_chunks`.

    The workflow VM caps a single boundary-crossing array at 4096 elements, so a
    full decision manifest (9600 units) must come back chunked. Older results —
    and any hand-assembled recovery file — are still flat.
    """
    out = dict(data)
    for key in ("cases", "decisions"):
        chunks = out.pop(f"{key}_chunks", None)
        if chunks is not None and not out.get(key):
            out[key] = [unit for chunk in chunks for unit in chunk]
    return out


def ingest(run_id: str, input_path: str, model: str) -> None:
    """Ingest a translate workflow result ({cases: [...], decisions: [...]}).

    Idempotent: dedupes on votering_id / cid; every record is validated for
    alignment against its Swedish source before it is written.
    """
    data = json.loads(open(input_path).read())
    data = _flatten_chunks(data)
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
    # Agents transcribe the cid by hand and occasionally change the case of the
    # votering_id's hex ("...-Ed93-..." for "...-ED93-..."), which threw away an
    # otherwise perfect translation. Lowercasing is collision-free across the
    # run's cids, so fall back to it; anything worse (a dropped character) still
    # fails and is simply re-issued by the next `translate-prepare`.
    by_lower = {cid.lower(): cid for cid in decisions}
    done_decisions = set(load_decision_translations(run_id))
    dpath = decisions_path(run_id)
    dpath.parent.mkdir(parents=True, exist_ok=True)
    with open(dpath, "a") as f:
        for rec in data.get("decisions", []):
            cid = rec.get("cid")
            if cid not in decisions and isinstance(cid, str):
                cid = by_lower.get(cid.lower(), cid)
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
            # `cid`, not `rec["cid"]` — store the canonical form the checkpoint
            # is keyed on, or a case-repaired row stays pending forever
            row = {k: rec[k] for k in ("motivering", "citations", "omvarld")}
            row |= {"cid": cid, "model": model, "collected_at": now}
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


# Swedish function words that no English translation should contain. Structural
# validation only checks array alignment, so it passed 388 rows whose motivering
# and quotes were English but whose `princip` chips were still Swedish
# ("förtroende mellan polis och allmänhet") — invisible until the English page
# renders them. These words have no English homographs, so a hit is a real miss.
_SWEDISH_MARKERS = re.compile(
    r"\b(och|att|inte|för|som|är|det|den|planen|motförslaget|valmanifestet|partiprogrammet)\b"
)


def untranslated_fields(rec: dict) -> list[str]:
    """Names of this translation record's fields that still read as Swedish."""
    out = []
    if _SWEDISH_MARKERS.search(rec.get("motivering") or ""):
        out.append("motivering")
    for c in rec.get("citations") or []:
        for field in ("quote", "princip"):
            if _SWEDISH_MARKERS.search(c.get(field) or ""):
                out.append(f"citations.{field}")
    for f in rec.get("omvarld") or []:
        for field in ("faktor", "effekt"):
            if _SWEDISH_MARKERS.search(f.get(field) or ""):
                out.append(f"omvarld.{field}")
    return sorted(set(out))


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
        # alignment can pass on a record that was never actually translated
        untranslated = {cid: f for cid, rec in dtrs.items() if (f := untranslated_fields(rec))}
        fields = sorted({f for fs in untranslated.values() for f in fs})
        yield (
            "decision translations are English",
            not untranslated,
            f"{len(untranslated)} rows still Swedish"
            + (f" (in {', '.join(fields)})" if fields else ""),
        )
