"""Reservation (Nej-alternative) substance, as pipeline data.

In the vote the counter-proposal is shown only as "Reservation N" — the agent
(and a reader) never sees what the Nej alternative actually proposes. This layer
recovers that substance from the betänkande fulltext and turns it into a short,
PARTY-BLIND summary of what each reservation wants, so it can be attached to the
case (site display now; wired into the simulation prompt later — a separate
Phase-3 re-run, out of scope here).

Pipeline (mirrors translate.py / metadata.py; no API key — Claude Code Workflow):
  fetch-reservations  network + deterministic: fetch each betänkande fulltext
                      (cached), parse each reservation's `Ställningstagande`
                      argument, scrub authorship -> data/interim extracted units
  reservations-prepare  pack pending extracted units into request files + manifest
  <Workflow haiku>      one party-blind {sv,en} summary per reservation
  reservations-ingest   validate (de-leak) + dedupe + append to results JSONL
  reservations-status / verify reservations

Storage (append-only JSONL, committed, run-INDEPENDENT):
  data/results/reservations/cases.jsonl   keyed by (votering_id, alt_id)

The de-leak GUARANTEE is the SCRUBBED input (the argument body run through
promptgen.scrub_text + party tags/names removed, authorship never sent); the
validator is a tested tripwire, reusing the same controls as the prompt gate.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone

import httpx
import polars as pl

from aidag.config import INTERIM_DIR, PARTIES, PROCESSED_DIR, RAW_DIR, RESULTS_DIR

RESERVATIONS_DIR = RESULTS_DIR / "reservations"
FETCH_CACHE = RAW_DIR / "betankande"          # raw fulltexts (gitignored)
EXTRACTED = INTERIM_DIR / "reservations" / "extracted.jsonl"
PER_REQUEST = 12                               # reservations per Haiku agent
FULLTEXT_URL = "https://data.riksdagen.se/dokument/{dok_id}.text"
HEADERS = {"User-Agent": "aidag-research/0.1 (open research on Riksdag votes)"}


def cases_path():
    return RESERVATIONS_DIR / "cases.jsonl"


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def load_reservations() -> dict[str, dict]:
    """All reservation summaries, keyed by 'votering_id:alt_id'."""
    return {f"{r['votering_id']}:{r['alt_id']}": r for r in _read_jsonl(cases_path())}


# ---------------------------------------------------------------------------
# fetch + parse the Ställningstagande (argument) out of the betänkande fulltext
# ---------------------------------------------------------------------------
def fetch_fulltext(dok_id: str) -> str:
    FETCH_CACHE.mkdir(parents=True, exist_ok=True)
    path = FETCH_CACHE / f"{dok_id}.text"
    if path.exists():
        return path.read_text()
    r = httpx.get(FULLTEXT_URL.format(dok_id=dok_id), headers=HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    path.write_text(r.text)
    time.sleep(0.4)  # be polite to data.riksdagen.se
    return r.text


def _plain(fulltext: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(fulltext))).strip()


_PARTI_GRP = r"([A-ZÅÄÖ]{1,3}(?:,\s*[A-ZÅÄÖ]{1,3})*)"
# Each reservation has the signature "…(PARTI). Förslag till riksdagsbeslut
# <decision> Ställningstagande <argument>", ending at the next such seam / Bilaga
# / end. The party right before "Förslag till riksdagsbeslut" is the RESERVATION's
# author (not a motion author cited inside the decision); the committee's own
# top-of-document FTR is not preceded by "(PARTI).", so it is skipped.
_RES_RE = re.compile(
    r"\(" + _PARTI_GRP + r"\)\s*\.\s*Förslag till riksdagsbeslut"
    r".*?Ställningstagande(?P<body>.*?)"
    r"(?=Reservation\s+\d+\s*\(|Särskilt yttrande|\(" + _PARTI_GRP
    + r"\)\s*\.\s*Förslag till riksdagsbeslut|\bBilaga\b|\Z)",
    re.S,
)


def parse_reservations(fulltext: str) -> list[dict]:
    """Reservation argument bodies in document order, robust across templates.
    Each: {author_parti: [codes], stallningstagande: <argument body>}."""
    txt = _plain(fulltext)
    out: list[dict] = []
    for m in _RES_RE.finditer(txt):
        parti = [p.strip() for p in m.group(1).split(",") if p.strip() in PARTIES]
        body = re.sub(r"&#x[0-9a-f]+;", " ", m.group("body"))
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) >= 40:
            out.append({"author_parti": parti, "stallningstagande": body})
    return out


_PARTY_NAMES_RE = re.compile(r"\b(?:" + "|".join(re.escape(p["name"]) for p in PARTIES.values()) + r")\b")


def scrub_substance(body: str) -> str:
    """Regex removal of what we can before the Haiku pass: author clauses, doc
    refs, exact dates, party tags, and full party names."""
    from aidag.promptgen import PARTY_TAG_RE, scrub_text

    s = scrub_text(body, "anonymous")
    s = PARTY_TAG_RE.sub("", s)
    s = _PARTY_NAMES_RE.sub("partiet", s)
    return re.sub(r"\s+", " ", s).strip()


def _match_bodies(reservations: list[dict], parsed: list[dict]) -> list[tuple[dict, dict | None]]:
    """Pair each case reservation (res-N) to a parsed body: by author-party
    overlap (primary), else the next unused body in order."""
    used: set[int] = set()
    pairs = []
    for a in reservations:
        want = set(a.get("source_partier") or [])
        idx = next((i for i, b in enumerate(parsed) if i not in used and want & set(b["author_parti"])), None)
        if idx is None:
            idx = next((i for i in range(len(parsed)) if i not in used), None)
        if idx is not None:
            used.add(idx)
        pairs.append((a, parsed[idx] if idx is not None else None))
    return pairs


def _reservations_of(case: dict) -> list[dict]:
    alts = case["alternatives"]
    if isinstance(alts, str):
        alts = json.loads(alts)
    return [a for a in alts if a.get("alt_id") != "utskottet"]


def fetch_and_extract(force: bool = False, limit: int | None = None) -> None:
    """fetch-reservations: fetch each betänkande fulltext (cached), parse each
    reservation's argument, scrub, and append party-blind request units to the
    interim extracted.jsonl. Idempotent on (votering_id, alt_id)."""
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").sort("datum", "votering_id")
    EXTRACTED.parent.mkdir(parents=True, exist_ok=True)
    done = {(u["votering_id"], u["alt_id"]) for u in _read_jsonl(EXTRACTED)} if not force else set()
    if force and EXTRACTED.exists():
        EXTRACTED.unlink()

    n_cases = n_units = n_missing = n_fail = 0
    with open(EXTRACTED, "a") as f:
        for case in cases.iter_rows(named=True):
            reservations = _reservations_of(case)
            if not reservations or all((case["votering_id"], a["alt_id"]) in done for a in reservations):
                continue
            try:
                parsed = parse_reservations(fetch_fulltext(case["dok_id"]))
            except Exception as e:  # noqa: BLE001
                n_fail += 1
                print(f"  fetch/parse failed {case['votering_id']} ({case['dok_id']}): {e}")
                continue
            n_cases += 1
            for a, body in _match_bodies(reservations, parsed):
                key = (case["votering_id"], a["alt_id"])
                if key in done:
                    continue
                if not body:
                    n_missing += 1
                    continue
                done.add(key)
                f.write(json.dumps({
                    "votering_id": case["votering_id"],
                    "alt_id": a["alt_id"],
                    "topic": case["rubrik"],
                    "reservation_scrubbed": scrub_substance(body["stallningstagande"]),
                }, ensure_ascii=False) + "\n")
                n_units += 1
            if limit and n_cases >= limit:
                break
    print(f"extracted {n_units} reservation units from {n_cases} cases "
          f"({n_missing} without a parsable Ställningstagande, {n_fail} fetch/parse failures)")


# ---------------------------------------------------------------------------
# prepare / status
# ---------------------------------------------------------------------------
INSTRUCTIONS = (
    "Each unit is a Riksdag counter-proposal (a reservation) with {topic, "
    "reservation_scrubbed}. For each, write ONE short neutral sentence naming what "
    "the counter-proposal WANTS — its substantive demand — grounded ONLY in "
    "reservation_scrubbed, using topic for context. Produce it in Swedish (sv) and "
    "English (en). Rules: PARTY-BLIND (name no party, no politician), present tense, "
    "no outcome, no document numbers; start the sv with \"Motförslaget vill\" or "
    "similar. One output unit per input unit, copying votering_id and alt_id. Answer "
    "only via structured output."
)


def _run_dir():
    return INTERIM_DIR / "reservations"


def _pack(units: list[dict], per_request: int) -> list[list[dict]]:
    return [units[i : i + per_request] for i in range(0, len(units), per_request)]


def _pending() -> list[dict]:
    """Extracted units not yet summarized in results/cases.jsonl."""
    done = set(load_reservations())
    return [
        {k: u[k] for k in ("votering_id", "alt_id", "topic", "reservation_scrubbed")}
        for u in _read_jsonl(EXTRACTED)
        if f"{u['votering_id']}:{u['alt_id']}" not in done
    ]


def prepare(batch_size: int = 400, per_request: int = PER_REQUEST) -> None:
    """Write the next reservation-summary batch manifest from pending extracted
    units. Run-independent, single-kind (no run_id / kind on the manifest)."""
    pending = _pending()
    groups = _pack(pending, per_request)
    if not groups:
        print("nothing pending — run fetch-reservations first, or summaries complete")
        return
    base = _run_dir()
    (base / "reqs").mkdir(parents=True, exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)
    batch_groups = groups[:batch_size]
    existing = sorted((base / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1
    items = []
    for i, units in enumerate(batch_groups):
        req = base / "reqs" / f"batch-{n:03d}-{i:04d}.json"
        req.write_text(json.dumps({"instructions": INSTRUCTIONS, "units": units}, ensure_ascii=False))
        items.append({"path": str(req), "n_units": len(units)})
    manifest = base / "batches" / f"batch-{n:03d}.json"
    manifest.write_text(json.dumps({"n_items": len(items), "items": items}, ensure_ascii=False))
    n_units = sum(i["n_units"] for i in items)
    print(f"reservations batch manifest: {manifest}")
    print(f"  {len(items)} agents: {n_units} reservation units")
    print(f"  remaining after this batch: {len(pending) - n_units}")


def status() -> None:
    n_extracted = len(_read_jsonl(EXTRACTED))
    n_done = len(load_reservations())
    total_res = sum(len(_reservations_of(c)) for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True))
    print(f"reservations: {total_res} in the corpus | {n_extracted} extracted | "
          f"{n_done} summarized | {n_extracted - n_done} pending")


# ---------------------------------------------------------------------------
# validate + ingest + verify
# ---------------------------------------------------------------------------
def _assert_clean(text: str, field: str) -> None:
    """Reuse the prompt/metadata de-leak controls as a tripwire."""
    from aidag.metadata import OUTCOME_RE, PARTY_NAMES_RE, PARTY_TAG_RE
    from aidag.promptgen import AUTHOR_RE, DOCREF_RE, FORBIDDEN_PATTERNS

    for label, rx in (
        ("party tag", PARTY_TAG_RE), ("party name", PARTY_NAMES_RE),
        ("author clause", AUTHOR_RE), ("outcome word", OUTCOME_RE), ("document ref", DOCREF_RE),
    ):
        m = rx.search(text)
        if m:
            raise ValueError(f"{field} leaks {label}: {m.group()!r}")
    for pattern in FORBIDDEN_PATTERNS:
        m = re.search(pattern, text)
        if m:
            raise ValueError(f"{field} leaks forbidden pattern: {m.group()!r}")


def validate_reservation(rec: dict) -> None:
    """Raise if a reservation summary is malformed or leaks party identity."""
    for lang in ("sv", "en"):
        val = str((rec.get("subject") or {}).get(lang, "")).strip()
        if not val:
            raise ValueError(f"empty subject.{lang}")
        _assert_clean(val, f"subject.{lang}")


def ingest(input_path: str, model: str) -> None:
    """Ingest a reservations workflow result ({reservations: [...]}). Validated,
    deduped on (votering_id, alt_id), idempotent."""
    data = json.loads(open(input_path).read())
    now = datetime.now(timezone.utc).isoformat()
    valid_keys = {
        (c["votering_id"], a["alt_id"])
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
        for a in _reservations_of(c)
    }
    done = set(load_reservations())
    n_ok = n_bad = 0
    cases_path().parent.mkdir(parents=True, exist_ok=True)
    with open(cases_path(), "a") as f:
        for rec in data.get("reservations", []):
            vid, alt = rec.get("votering_id"), rec.get("alt_id")
            key = f"{vid}:{alt}"
            if not vid or not alt or key in done:
                continue
            if (vid, alt) not in valid_keys:
                n_bad += 1
                print(f"  skipped {key}: not a real reservation")
                continue
            try:
                validate_reservation(rec)
            except Exception as e:  # noqa: BLE001
                n_bad += 1
                print(f"  skipped {key}: {e}")
                continue
            done.add(key)
            f.write(json.dumps({
                "votering_id": vid, "alt_id": alt, "subject": rec["subject"],
                "model": model, "collected_at": now,
            }, ensure_ascii=False) + "\n")
            n_ok += 1
    print(f"ingested {n_ok} reservation summaries ({n_bad} skipped)")


def verify_reservations(run_id: str | None = None):
    """Checks for `aidag verify reservations`. Yields (name, ok, detail).
    Run-independent (run_id ignored)."""
    valid_keys = {
        (c["votering_id"], a["alt_id"])
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
        for a in _reservations_of(c)
    }
    recs = load_reservations()
    bad = unknown = 0
    for key, rec in recs.items():
        if (rec["votering_id"], rec["alt_id"]) not in valid_keys:
            unknown += 1
            continue
        try:
            validate_reservation(rec)
        except Exception:  # noqa: BLE001
            bad += 1
    yield (
        "reservation summaries valid + party-blind",
        bad == 0,
        f"{len(recs)} summaries, {bad} invalid/leaky",
    )
    yield ("every reservation id is a real reservation", unknown == 0, f"{unknown} unknown ids")
