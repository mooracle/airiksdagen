"""Per-case metadata layer: deterministic facts + grounded cheap-LLM synthesis.

A run-INDEPENDENT layer that describes each *case* (not any AI run), so a reader
instantly understands a vote, cases become searchable/filterable by topic, and a
party-blind "agent view" is staged for a later (separate) simulation re-run.

Two kinds of field, produced by two mechanisms:
  - deterministic (`extract_deterministic`): type/policy_area/committee/counts/
    parties — zero LLM, so they can never be fabricated and are re-attached
    server-side at ingest.
  - synthesis (Haiku 4.5 via scripts/metadata_batch_workflow.js): subject,
    what's-at-stake, subtopics, and a de-leaked agent view — grounded ONLY on
    the request file each agent reads.

Storage (append-only JSONL, committed like translations):
  data/results/metadata/cases.jsonl   run-INDEPENDENT, keyed by votering_id

Checkpoint mirrors translate.py: done = ids present in the JSONL, pending =
everything else, `metadata-prepare` always emits the next batch.

The de-leak GUARANTEE for the agent view is the SCRUBBED input (`agent_src`,
built with promptgen.scrub_text(..., "anonymous") and parties dropped); the
validator (`validate_metadata`) is a tested tripwire, not the guarantee. This
module changes NOTHING in promptgen.py and does not re-run the simulation.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

import polars as pl

from aidag.config import INTERIM_DIR, PARTIES, PARTY_CODES, PROCESSED_DIR, RESULTS_DIR

METADATA_DIR = RESULTS_DIR / "metadata"
CASES_PER_REQUEST = 12  # cases bundled into one synthesis agent's request file


def cases_path():
    return METADATA_DIR / "cases.jsonl"


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_metadata() -> dict[str, dict]:
    """All metadata records, keyed by votering_id."""
    return {r["votering_id"]: r for r in _read_jsonl(cases_path())}


# ---------------------------------------------------------------------------
# utskott -> policy_area
#
# `policy_area` is stored as the stable `code` (a language-independent filter
# key). The sv/en labels live here and are surfaced to the site (meta.json /
# i18n), so the filter key never differs per build language. Several committees
# may share a policy_area code (FöU + the joint UFöU -> "defense"); that folds
# near-empty filter options into their natural neighbour. Keys are the exact
# casing found in cases.parquet (MJU, FöU, UFöU, …), matched case-insensitively.
# ---------------------------------------------------------------------------
UTSKOTT_AREA: dict[str, dict] = {
    "AU":   {"code": "labor",        "sv": "Arbetsmarknad",           "en": "Labour market"},
    "CU":   {"code": "civil",        "sv": "Civilrätt och bostäder",  "en": "Civil law & housing"},
    "FiU":  {"code": "finance",      "sv": "Ekonomi och finans",      "en": "Economy & finance"},
    "FöU":  {"code": "defense",      "sv": "Försvar",                 "en": "Defence"},
    "JuU":  {"code": "justice",      "sv": "Rättsväsende",            "en": "Justice"},
    "KrU":  {"code": "culture",      "sv": "Kultur",                  "en": "Culture"},
    "KU":   {"code": "constitution", "sv": "Konstitution",            "en": "Constitution"},
    "MJU":  {"code": "environment",  "sv": "Miljö och jordbruk",      "en": "Environment & agriculture"},
    "NU":   {"code": "business",     "sv": "Näringsliv och energi",   "en": "Business & energy"},
    "SfU":  {"code": "migration",    "sv": "Migration och socialförsäkring", "en": "Migration & social insurance"},
    "SkU":  {"code": "tax",          "sv": "Skatter",                 "en": "Taxation"},
    "SoU":  {"code": "health",       "sv": "Hälsa och socialtjänst",  "en": "Health & social services"},
    "TU":   {"code": "transport",    "sv": "Transport och infrastruktur", "en": "Transport & infrastructure"},
    "UbU":  {"code": "education",    "sv": "Utbildning",              "en": "Education"},
    "UU":   {"code": "foreign",      "sv": "Utrikes",                 "en": "Foreign affairs"},
    "UFöU": {"code": "defense",      "sv": "Försvar",                 "en": "Defence"},
}
OTHER_AREA = {"code": "ovrigt", "sv": "Övrigt", "en": "Other"}


def area_for(utskott: str) -> dict:
    """Committee code -> {code, sv, en}; case-insensitive, Other fallback."""
    u = (utskott or "").strip()
    if u in UTSKOTT_AREA:
        return UTSKOTT_AREA[u]
    lut = {k.upper(): v for k, v in UTSKOTT_AREA.items()}
    return lut.get(u.upper(), OTHER_AREA)


def policy_area_labels() -> dict[str, dict]:
    """{policy_area code -> {sv, en}} for the site's localized filter labels."""
    out: dict[str, dict] = {}
    for area in UTSKOTT_AREA.values():
        out.setdefault(area["code"], {"sv": area["sv"], "en": area["en"]})
    out.setdefault(OTHER_AREA["code"], {"sv": OTHER_AREA["sv"], "en": OTHER_AREA["en"]})
    return out


# ---------------------------------------------------------------------------
# deterministic extraction (zero LLM)
# ---------------------------------------------------------------------------
# Budget framework votes: the FiU1 rambeslut, plus any expenditure-frame /
# appropriation phrasing. `\bproposition\b` on its own is NOT a budget signal.
_BUDGET_RE = re.compile(r"utgiftsram|utgiftstak|anvisar\s+anslag|finansplan|rambeslut", re.IGNORECASE)
# A committee proposal that adopts/approves the government's own bill.
_PROP_RE = re.compile(
    r"antar regeringens förslag|godkänner regeringens förslag|bifaller\s+(?:riksdagen\s+)?proposition",
    re.IGNORECASE,
)
_MOTION_RE = re.compile(r"avslår motion|bifaller motion", re.IGNORECASE)
# Party codes inside an author paren, e.g. "(båda C)" / "(S, MP)" / "(V)".
_AUTHOR_PARTY_RE = re.compile(
    r"\((?:båda\s+|samtliga\s+|bådas\s+)?([A-ZÅÄÖ]{1,3}(?:\s*,\s*[A-ZÅÄÖ]{1,3})*)\)"
)


def _as_list(value) -> list:
    if isinstance(value, str):
        return json.loads(value) if value.strip() else []
    return value or []


def _is_budget(forslag: str, beteckning: str) -> bool:
    return bool(_BUDGET_RE.search(forslag)) or (beteckning or "").strip() == "FiU1"


def _classify_type(forslag: str, is_budget: bool) -> str:
    """Best-effort case type with explicit precedence. Real betänkanden mix
    phrases (a proposition that also rejects a motion), so order matters:
    budget -> proposition -> motion -> other."""
    if is_budget:
        return "budget"
    if _PROP_RE.search(forslag):
        return "proposition"
    if _MOTION_RE.search(forslag):
        return "motion"
    return "other"


def _parties_involved(alternatives: list[dict], forslag: str) -> list[str]:
    """DISPLAY-ONLY. PRIMARY: reservation `source_partier` (structured, reliable).
    Fallback: scrape `forslag_text` author clauses. A miss is cosmetic."""
    parties: list[str] = []
    for a in alternatives:
        if a.get("alt_id") == "utskottet":
            continue
        for p in a.get("source_partier") or []:
            if p in PARTY_CODES and p not in parties:
                parties.append(p)
    if parties:
        return parties
    # AUTHOR_RE has no capturing groups, so findall yields whole author clauses;
    # pull the party codes out of each clause's trailing paren.
    from aidag.promptgen import AUTHOR_RE

    for clause in AUTHOR_RE.findall(forslag):
        for grp in _AUTHOR_PARTY_RE.findall(clause):
            for code in re.split(r"\s*,\s*", grp):
                if code in PARTY_CODES and code not in parties:
                    parties.append(code)
    return parties


def extract_deterministic(case: dict) -> dict:
    """Structured facts about a case, zero LLM.

    `type` and `parties_involved` are best-effort (documented as such); the rest
    are exact. HTML entities in later-riksmöte prose are unescaped so phrase
    matching stays reliable.
    """
    forslag = html.unescape(case.get("forslag_text") or "")
    beteckning = case.get("beteckning") or ""
    alternatives = _as_list(case.get("alternatives"))
    references = _as_list(case.get("references"))
    is_budget = _is_budget(forslag, beteckning)
    return {
        "type": _classify_type(forslag, is_budget),
        "policy_area": area_for(case.get("utskott", "")).get("code"),
        "committee": (case.get("utskott") or "").strip(),
        "n_motions": sum(1 for r in references if r.get("typ") == "mot"),
        "n_reservations": sum(1 for a in alternatives if a.get("alt_id") != "utskottet"),
        "is_budget": is_budget,
        "parties_involved": _parties_involved(alternatives, forslag),
    }


# ---------------------------------------------------------------------------
# grounded request units + prepare()/status()
#
# Every unit carries TWO grounding blocks so the party-blind agent fields can
# never be synthesized from party-aware text:
#   - display_src  party-aware, feeds the human subject/at_stake/subtopics
#   - agent_src    SCRUBBED (promptgen.scrub_text(..., "anonymous"), parties and
#                  author names dropped) — the ONLY input the agent.* fields may use
# The post-decision `notis` is never included in either block.
# ---------------------------------------------------------------------------
_FORSLAG_MAX = 2000   # chars of committee-proposal prose per unit
_RES_MAX = 600        # chars per reservation text
_N_REFS = 15          # reference titles surfaced for topical grounding

INSTRUCTIONS = (
    "For each unit, write concise, neutral metadata for a public research site about "
    "Sweden's Riksdag votes. Produce, PER UNIT:\n"
    "- subject{sv,en}: ONE plain sentence naming what THIS vote is concretely about (the "
    "actual policy question), grounded in `display_src`. Not committee boilerplate — be "
    "specific and readable.\n"
    "- subtopics: 2-5 short lowercase Swedish topic keywords for search/filter (e.g. "
    "\"migration\", \"a-kassa\", \"kärnkraft\"), from `display_src`.\n"
    "- at_stake{sv,en}: 1-2 sentences on what is materially at stake / why this vote matters "
    "(the real-world consequence). Do NOT restate the literal Ja/Nej procedure — that is "
    "provided separately. Grounded in `display_src`.\n"
    "- agent{subject,at_stake}: the SAME subject + at_stake, but written ONLY from `agent_src` "
    "(the scrubbed input). Must be PARTY-BLIND and PRE-DECISION: name no party, no politician, "
    "no document number, no date. Write it in the PRESENT tense as a neutral topic description "
    "of what the vote is about; state NO outcome and NEVER use a past-tense result verb "
    "(avslogs, antogs, godkändes, biföll, röstade igenom, beslutade) — the vote has not happened "
    "yet. One short sentence each.\n"
    "Ground every field strictly in the unit's own text; invent no facts. Keep sv in Swedish "
    "and en in English. Answer only via structured output, one output unit per input unit, "
    "copying votering_id unchanged."
)


def _clean(text: str) -> str:
    """Unescape HTML entities and collapse whitespace."""
    return " ".join(html.unescape(text or "").split())


def _trunc(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _case_unit(case: dict) -> dict:
    """One synthesis request unit: display_src (party-aware) + agent_src (scrubbed)."""
    from aidag.promptgen import scrub_text

    rubrik = _clean(case.get("rubrik") or "")
    utskott = (case.get("utskott") or "").strip()
    utsknotis = _trunc(case.get("utsknotis") or "", _FORSLAG_MAX)
    forslag = _trunc(case.get("forslag_text") or "", _FORSLAG_MAX)
    alternatives = _as_list(case.get("alternatives"))
    reservations = [
        _trunc(a.get("text") or "", _RES_MAX)
        for a in alternatives
        if a.get("alt_id") != "utskottet"
    ]
    references = _as_list(case.get("references"))
    ref_titles = [_clean(r.get("titel") or r.get("label") or "") for r in references[:_N_REFS]]
    ref_titles = [t for t in ref_titles if t]

    display_src = {
        "rubrik": rubrik,
        "utskott": utskott,
        "summary": _clean(utsknotis),
        "forslag_text": forslag,
        "reservations": reservations,
        "references": ref_titles,
    }
    # SCRUBBED: forslag/reservations/ref-titles run through the anonymous-arm
    # scrub (drops author clauses + party tags, doc numbers, exact dates); the
    # structured party list (source_partier / parties_involved) is simply omitted.
    agent_src = {
        "rubrik": scrub_text(rubrik, "anonymous"),
        "utskott": utskott,
        "forslag_text": scrub_text(forslag, "anonymous"),
        "reservations": [scrub_text(r, "anonymous") for r in reservations],
        "references": [scrub_text(t, "anonymous") for t in ref_titles],
    }
    return {"votering_id": case["votering_id"], "display_src": display_src, "agent_src": agent_src}


def _pack(units: list[dict], per_request: int) -> list[list[dict]]:
    return [units[i : i + per_request] for i in range(0, len(units), per_request)]


def _run_dir():
    return INTERIM_DIR / "metadata"


def _pending() -> list[dict]:
    """Request units for every case in parquet not yet in cases.jsonl."""
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").sort("datum", "votering_id")
    done = set(load_metadata())
    return [_case_unit(c) for c in cases.iter_rows(named=True) if c["votering_id"] not in done]


def prepare(batch_size: int = 400, per_request: int = CASES_PER_REQUEST) -> None:
    """Write the NEXT metadata batch manifest from whatever is pending.

    One manifest item = one request file = one synthesis agent handling several
    cases. The manifest is run-INDEPENDENT and single-kind, so it omits both the
    `run_id` and the `kind` split that translate's manifest carries. `batch_size`
    caps the number of agents (request files); the default fans out over ALL
    pending cases.
    """
    pending = _pending()
    groups = _pack(pending, per_request)
    if not groups:
        print("nothing pending — metadata complete")
        return

    base = _run_dir()
    (base / "reqs").mkdir(parents=True, exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)

    batch_groups = groups[:batch_size]
    existing = sorted((base / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1

    items = []
    for i, units in enumerate(batch_groups):
        req_path = base / "reqs" / f"batch-{n:03d}-{i:04d}.json"
        req_path.write_text(
            json.dumps({"instructions": INSTRUCTIONS, "units": units}, ensure_ascii=False)
        )
        items.append({"path": str(req_path), "n_units": len(units)})

    manifest_path = base / "batches" / f"batch-{n:03d}.json"
    manifest_path.write_text(
        json.dumps({"n_items": len(items), "items": items}, ensure_ascii=False)
    )
    n_units = sum(i["n_units"] for i in items)
    print(f"metadata batch manifest: {manifest_path}")
    print(f"  {len(items)} agents: {n_units} case units")
    print(f"  remaining after this batch: {len(pending) - n_units} cases")


def status() -> None:
    pending = _pending()
    total = pl.read_parquet(PROCESSED_DIR / "cases.parquet").height
    print(f"metadata: {total - len(pending)}/{total} done, {len(pending)} pending")


# ---------------------------------------------------------------------------
# validation (grounding + de-leak asserts) + ingest + verify
#
# De-leak checks REUSE existing controls rather than reinventing them:
#   - promptgen.FORBIDDEN_PATTERNS  beteckning / dok_id / UUID / ISO date
#   - promptgen.AUTHOR_RE           "…av <name> (<party>)" author clauses
#   - PARTY_TAG_RE                  "(S)" … "(MP)"
#   - PARTY_NAMES_RE               the eight full party names from config.PARTIES
#   - OUTCOME_RE                   enumerated post-decision result words
# The validator is a TRIPWIRE for regressions; the real guarantee that agent.*
# is safe to wire into the prompt later is the SCRUBBED `agent_src` input.
# ---------------------------------------------------------------------------
PARTY_TAG_RE = re.compile(r"\((?:S|M|SD|C|V|KD|MP|L)\)")
PARTY_NAMES_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p["name"]) for p in PARTIES.values()) + r")\b",
    re.IGNORECASE,
)
# Post-decision outcome words: the committee PROPOSAL text is present-tense
# ("Riksdagen avslår / antar / godkänner"); the OUTCOME is past-tense
# ("avslog / antogs / godkändes / biföll") or an explicit result phrase. Keying
# on the decided forms lets a pre-decision agent view through and catches leaks.
OUTCOME_RE = re.compile(
    r"\b(?:sa(?:de)?\s+ja|sa(?:de)?\s+nej|biföll(?:s|es)?|avslog(?:s)?|godkändes|"
    r"antogs|beslutade(?:s)?|röstade\s+igenom|röstades\s+(?:ned|igenom)|"
    r"fick\s+majoritet|vann\s+omröstningen|avslag\s+på\s+propositionen)\b",
    re.IGNORECASE,
)


def _assert_agent_clean(text: str, field: str) -> None:
    """Raise if a party-blind agent field leaks identity/authorship/outcome."""
    from aidag.promptgen import AUTHOR_RE, DOCREF_RE, FORBIDDEN_PATTERNS

    for label, rx in (
        ("party tag", PARTY_TAG_RE),
        ("party name", PARTY_NAMES_RE),
        ("author clause", AUTHOR_RE),
        ("outcome word", OUTCOME_RE),
        # DOCREF_RE catches bare doc numbers ("2022/23:85") that FORBIDDEN_PATTERNS
        # (beteckning WITH letters, "2023/24:AU10") misses — same key scrub_text drops.
        ("document ref", DOCREF_RE),
    ):
        m = rx.search(text)
        if m:
            raise ValueError(f"agent.{field} leaks {label}: {m.group()!r}")
    for pattern in FORBIDDEN_PATTERNS:
        m = re.search(pattern, text)
        if m:
            raise ValueError(f"agent.{field} leaks forbidden pattern: {m.group()!r}")


def validate_metadata(rec: dict, unit: dict | None = None) -> None:
    """Raise if a synthesis record is malformed or the agent view leaks.

    `unit` (optional) enables a votering_id alignment check. The de-leak asserts
    on `agent.*` are the tripwire; the guarantee is the scrubbed `agent_src`.
    """
    if unit is not None and rec.get("votering_id") != unit.get("votering_id"):
        raise ValueError("votering_id mismatch")
    for field in ("subject", "at_stake"):
        block = rec.get(field) or {}
        for lang in ("sv", "en"):
            if not str(block.get(lang, "")).strip():
                raise ValueError(f"empty {field}.{lang}")
    if not isinstance(rec.get("subtopics"), list):
        raise ValueError("subtopics must be a list")
    agent = rec.get("agent") or {}
    for field in ("subject", "at_stake"):
        val = str(agent.get(field, "")).strip()
        if not val:
            raise ValueError(f"empty agent.{field}")
        _assert_agent_clean(val, field)


_DET_FIELDS = ("type", "policy_area", "committee", "n_motions", "n_reservations", "is_budget", "parties_involved")


def ingest(input_path: str, model: str) -> None:
    """Ingest a metadata workflow result ({cases: [...]}).

    Each record is validated (fields + agent-view de-leak), merged with the
    server-side `extract_deterministic` fields (so the model can't fabricate
    type/policy_area/parties), deduped on votering_id, and appended with
    provenance. Idempotent: re-ingesting the same result adds nothing.
    """
    data = json.loads(open(input_path).read())
    now = datetime.now(timezone.utc).isoformat()
    cases_by_vid = {
        c["votering_id"]: c
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
    }
    done = set(load_metadata())
    n_ok = n_bad = 0

    cases_path().parent.mkdir(parents=True, exist_ok=True)
    with open(cases_path(), "a") as f:
        for rec in data.get("cases", []):
            vid = rec.get("votering_id")
            if not vid or vid in done:
                continue
            if vid not in cases_by_vid:
                n_bad += 1
                print(f"  skipped {vid}: not in cases.parquet")
                continue
            try:
                validate_metadata(rec)
            except Exception as e:  # noqa: BLE001
                n_bad += 1
                print(f"  skipped {vid}: {e}")
                continue
            done.add(vid)
            row = {"votering_id": vid}
            row |= extract_deterministic(cases_by_vid[vid])
            row |= {
                "subject": rec["subject"],
                "at_stake": rec["at_stake"],
                "subtopics": rec.get("subtopics", []),
                "agent": rec["agent"],
                "model": model,
                "collected_at": now,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n_ok += 1

    print(f"ingested {n_ok} metadata records ({n_bad} skipped)")


def verify_metadata(run_id: str | None = None):
    """Checks for `aidag verify metadata`. Yields (name, ok, detail).

    Run-independent (run_id ignored) — accepts the arg only so the verify
    dispatcher can call every stage uniformly.
    """
    cases_by_vid = {
        c["votering_id"]: c
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
    }
    recs = load_metadata()

    bad = unknown = 0
    for vid, rec in recs.items():
        if vid not in cases_by_vid:
            unknown += 1
            continue
        try:
            validate_metadata(rec)
        except Exception:  # noqa: BLE001
            bad += 1
    yield (
        "metadata records valid + agent view clean",
        bad == 0,
        f"{len(recs)} records, {bad} invalid/leaky",
    )
    yield ("every metadata id exists in parquet", unknown == 0, f"{unknown} unknown ids")

    mism = 0
    for vid, rec in recs.items():
        if vid not in cases_by_vid:
            continue
        det = extract_deterministic(cases_by_vid[vid])
        if any(rec.get(k) != det.get(k) for k in _DET_FIELDS):
            mism += 1
    yield ("deterministic fields align with source cases", mism == 0, f"{mism} misaligned")
