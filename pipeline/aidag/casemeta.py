"""Unified per-case metadata — the rebuilt, self-contained open dataset.

Replaces the piecemeal metadata/reservations/committee layers with ONE solid record
per votering, built from the betänkande fulltext with robust, document-native
associations:

  - reservations  -> by NUMBER (`res-N` ↔ "Reservation N. <rubrik>, punkt P (PARTI)"),
                     cross-checked on punkt + party. (The old party-overlap match
                     mis-paired when a party authored ≥2 reservations in a betänkande.)
  - committee (Ja) -> top-K `Utskottets ställningstagande` blocks by motion-id coverage;
                     the synthesis agent picks the one that answers THIS point.

Each record carries a party-AWARE display half (subject/decision/at_stake/ja/nej) and a
party-BLIND `agent` half (committee/alternatives) built only from scrubbed text, so the
same dataset serves the site, the open download, AND the simulation prompt. The de-leak
validator is the tripwire; the scrubbed `agent_src` is the guarantee.
"""

from __future__ import annotations

import html
import json
import re

import polars as pl

from datetime import datetime, timezone

from aidag.committee import parse_committee_blocks, rank_candidates
from aidag.config import INTERIM_DIR, PROCESSED_DIR, RESULTS_DIR
from aidag.reservations import _assert_clean, _read_jsonl, fetch_fulltext, scrub_substance

CASEMETA_DIR = RESULTS_DIR / "casemeta"
INTERIM = INTERIM_DIR / "casemeta"


def cases_path():
    return CASEMETA_DIR / "cases.jsonl"


def load_casemeta() -> dict[str, dict]:
    return {r["votering_id"]: r for r in _read_jsonl(cases_path())}


def _plain(fulltext: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(fulltext))).strip()


# Reservation entries carry ", punkt P (PARTI)" in BOTH templates: numbered
# ("Reservation 16. Personalliggare, punkt 10 (C) av …", multi-reservation betänkanden)
# and unnumbered ("Reservation Ny påföljd…, punkt 2 (V, C, MP) av …", single-reservation
# ones). So (punkt, party) is the robust key, not the number.
# Both prefixes optional: "16. <rubrik>, punkt 10 (C) av …" (numbered list, multi) and
# "Reservation <rubrik>, punkt 2 (V, C, MP) av …" (worded, single). The
# "av … Förslag till riksdagsbeslut … Ställningstagande <body>" tail is what marks a real
# entry (vs a Förteckning/ToC line, which has no such tail).
_HDR = r"(?:Reservation\s+)?(?:\d+\.\s+)?[^,]{2,60}?,\s*punkt\s*\d+\s*\([A-ZÅÄÖ]"
_ENTRY_RE = re.compile(
    r"(?:Reservation\s+)?(?:(?P<num>\d+)\.\s+)?(?P<rubrik>[^,]{2,60}?),\s*punkt\s*(?P<punkt>\d+)\s*"
    r"\((?P<party>[A-ZÅÄÖ][A-ZÅÄÖ,\s]*?)\)\s+av\s+.*?\.\s*Förslag till riksdagsbeslut"
    r".*?Ställningstagande(?P<body>.*?)"
    r"(?=" + _HDR + r"|\bBilaga\b|Särskilt yttrande|\Z)",
    re.S,
)


def parse_reservations_indexed(fulltext: str) -> list[dict]:
    """All reservation bodies with their (num?, rubrik, punkt, party). Keyed later by
    punkt+party so it works across numbered and unnumbered betänkande templates."""
    txt = _plain(fulltext)
    out: list[dict] = []
    for m in _ENTRY_RE.finditer(txt):
        body = re.sub(r"&#x[0-9a-f]+;", " ", m.group("body"))
        out.append({
            "num": int(m.group("num")) if m.group("num") else None,
            "rubrik": m.group("rubrik").strip(),
            "punkt": int(m.group("punkt")),
            "party": [p.strip() for p in m.group("party").split(",") if p.strip()],
            "body": re.sub(r"\s+", " ", body).strip(),
        })
    return out


def match_reservation(entries: list[dict], punkt: int, partier: list[str] | None) -> dict | None:
    """The reservation body for a votering: entries on this punkt, disambiguated by the
    reservation's author parties when a punkt carries more than one."""
    same = [e for e in entries if e["punkt"] == punkt]
    if len(same) <= 1:
        return same[0] if same else None
    if partier:
        want = set(partier)
        best = max(same, key=lambda e: len(want & set(e["party"])))
        if want & set(best["party"]):
            return best
    return same[0]


def _reservations_of(case: dict) -> list[dict]:
    alts = case["alternatives"]
    if isinstance(alts, str):
        alts = json.loads(alts)
    return [a for a in alts if a.get("alt_id") != "utskottet"]


def build_packet(case: dict, blocks: list[dict], entries: list[dict]) -> dict:
    """Deterministic per-case source packet: parquet facts + Ja candidates + Nej bodies,
    in a party-aware `display_src` and a scrubbed `agent_src`. `flags` records association
    cross-checks (missing/party mismatch) for the validator to gate on."""
    want = set(re.findall(r"\d{4}/\d{2}:\d+", case.get("forslag_text") or ""))
    cands = rank_candidates(want, blocks, k=2)
    ja_display = [c["body"][:2200] for c in cands]
    ja_agent = [scrub_substance(c["body"])[:2200] for c in cands]
    # the committee's own proposal prose — context for every case, and the FALLBACK
    # source when a betänkande has no parsable ställningstagande/reservation blocks
    forslag = scrub_substance(_plain(case.get("forslag_text") or ""))[:1200]

    nej_display, nej_agent, flags = [], [], []
    for a in _reservations_of(case):
        e = match_reservation(entries, case["punkt"], a.get("source_partier"))
        if not e:
            flags.append(f"{a['alt_id']}: no entry on punkt {case['punkt']}")
            continue
        if a.get("source_partier") and not (set(e["party"]) & set(a["source_partier"])):
            flags.append(f"{a['alt_id']}: party {e['party']}!={a['source_partier']}")
        nej_display.append({"alt_id": a["alt_id"], "party": e["party"], "body": e["body"][:2200]})
        nej_agent.append({"alt_id": a["alt_id"], "body": scrub_substance(e["body"])[:2200]})

    fallback = not cands and not nej_display
    if fallback:
        flags.append("fallback: no committee/reservation blocks — grounded on förslag")
    return {
        "votering_id": case["votering_id"],
        "punkt": case["punkt"],
        "rubrik": case["rubrik"],
        "dok_titel": case.get("dok_titel"),
        "utskott": case["utskott"],
        "decided_motions": sorted(want),
        "fallback": fallback,
        "display_src": {"committee": ja_display, "reservations": nej_display, "forslag": forslag},
        "agent_src": {"committee": ja_agent, "reservations": nej_agent, "forslag": forslag},
        "flags": flags,
    }


def build_packets(votering_ids: list[str]) -> list[dict]:
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    by_dok: dict[str, list[dict]] = {}
    for vid in votering_ids:
        row = cases.filter(pl.col("votering_id") == vid).to_dicts()[0]
        by_dok.setdefault(row["dok_id"], []).append(row)
    packets = []
    for dok, rows in by_dok.items():
        full = fetch_fulltext(dok)
        blocks = parse_committee_blocks(full)
        resindex = parse_reservations_indexed(full)
        for row in rows:
            packets.append(build_packet(row, blocks, resindex))
    return packets


OUTPUT_SCHEMA_HINT = {
    "votering_id": "<copied>",
    "subject": {"sv": "…", "en": "…"},
    "subtopics": ["…"],
    "decision": {"sv": "…", "en": "…"},
    "at_stake": {"sv": "…", "en": "…"},
    "ja": {"sv": "…", "en": "…"},
    "nej": [{"alt_id": "res-N", "sv": "…", "en": "…"}],
    "agent": {
        "committee": {"sv": "…", "en": "…"},
        "alternatives": [{"alt_id": "res-N", "sv": "…", "en": "…"}],
    },
}

INSTRUCTIONS = (
    "You are building one open-dataset metadata record for a single Riksdag vote, for the "
    "general public. Inputs: {rubrik, decided_motions, display_src, agent_src}. display_src "
    "(committee reasoning candidates + reservation bodies, party-aware) grounds the human "
    "fields; agent_src (the SAME text, scrubbed) grounds the `agent` fields — the agent "
    "fields may use ONLY agent_src. Among the committee candidates, EXACTLY ONE answers this "
    "point (use rubrik + the reservations to judge); use it for `ja`/`agent.committee`. "
    "Fields: subject = specific bilingual title of what THIS vote decides; subtopics = 3-6 "
    "short tags; decision = one neutral sentence naming the contested axis (both sides); "
    "at_stake = 1-2 plain sentences on why it matters and who is affected; ja = the "
    "committee's position + main reason; nej[] = each reservation's demand + reason (copy "
    "alt_id); agent.committee + agent.alternatives[] = the same Ja/Nej substance but "
    "PARTY-BLIND from agent_src. RULES for agent.*: name no party/politician; present tense; "
    "no floor-vote outcome — do NOT use outcome words such as beslutade/beslutar/antog/"
    "biföll/avslog/röstade/tillkännager; no document numbers; no dates. Copy votering_id. "
    "sv + en for every text field."
)


# ---------------------------------------------------------------------------
# prepare / status / ingest / verify  (checkpoint-aware, run-INDEPENDENT)
# ---------------------------------------------------------------------------
def _pending_ids() -> list[str]:
    done = set(load_casemeta())
    allc = pl.read_parquet(PROCESSED_DIR / "cases.parquet").sort("datum", "votering_id")
    return [v for v in allc["votering_id"].to_list() if v not in done]


def prepare(batch_size: int = 500, per_request: int = 6) -> None:
    """Emit the next casemeta batch manifest from pending cases. Each request file bundles
    per_request case packets; one Sonnet agent per file writes {cases:[record,...]}."""
    pending = _pending_ids()
    if not pending:
        print("nothing pending — casemeta complete")
        return
    packets = build_packets(pending)
    groups = [packets[i : i + per_request] for i in range(0, len(packets), per_request)][:batch_size]
    (INTERIM / "reqs").mkdir(parents=True, exist_ok=True)
    (INTERIM / "batches").mkdir(exist_ok=True)
    existing = sorted((INTERIM / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1
    items = []
    for i, units in enumerate(groups):
        req = INTERIM / "reqs" / f"batch-{n:03d}-{i:04d}.json"
        req.write_text(json.dumps(
            {"instructions": INSTRUCTIONS, "schema_example": OUTPUT_SCHEMA_HINT, "units": units},
            ensure_ascii=False))
        items.append({"path": str(req), "n_units": len(units)})
    manifest = INTERIM / "batches" / f"batch-{n:03d}.json"
    manifest.write_text(json.dumps({"n_items": len(items), "items": items}, ensure_ascii=False))
    n_units = sum(it["n_units"] for it in items)
    print(f"casemeta batch manifest: {manifest}")
    print(f"  {len(items)} agents, prefix batch-{n:03d}-, {n_units} case records")
    print(f"  remaining after this batch: {len(packets) - n_units}")


def status() -> None:
    total = pl.read_parquet(PROCESSED_DIR / "cases.parquet").height
    done = len(load_casemeta())
    print(f"casemeta: {total} cases | {done} built | {total - done} pending")


def validate_record(rec: dict) -> None:
    """Schema + party-blind de-leak on the agent view."""
    for field in ("subject", "decision", "at_stake", "ja"):
        for lang in ("sv", "en"):
            if not str((rec.get(field) or {}).get(lang, "")).strip():
                raise ValueError(f"empty {field}.{lang}")
    for lang in ("sv", "en"):
        _assert_clean(rec["agent"]["committee"][lang], f"agent.committee.{lang}")
        for alt in rec["agent"].get("alternatives", []):
            _assert_clean(alt[lang], f"agent.alt.{lang}")


def ingest(input_path: str, model: str) -> None:
    """Ingest workflow output ({cases:[record,...]} files, or a dir of them). Validates +
    de-leaks the agent view, merges deterministic fields, dedupes on votering_id."""
    from pathlib import Path

    from aidag.metadata import extract_deterministic

    p = Path(input_path)
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    records = [r for f in files for r in json.loads(f.read_text()).get("cases", [])]
    cases = {c["votering_id"]: c for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)}
    done = set(load_casemeta())
    now = datetime.now(timezone.utc).isoformat()
    n_ok = n_bad = 0
    cases_path().parent.mkdir(parents=True, exist_ok=True)
    with open(cases_path(), "a") as f:
        for rec in records:
            vid = rec.get("votering_id")
            if not vid or vid in done:
                continue
            if vid not in cases:
                n_bad += 1
                continue
            try:
                validate_record(rec)
            except Exception as e:  # noqa: BLE001
                n_bad += 1
                print(f"  skipped {vid[:8]}: {e}")
                continue
            done.add(vid)
            out = {"votering_id": vid, **extract_deterministic(cases[vid]),
                   **{k: rec[k] for k in ("subject", "subtopics", "decision", "at_stake", "ja", "nej", "agent")},
                   "model": model, "collected_at": now}
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
            n_ok += 1
    print(f"ingested {n_ok} casemeta records ({n_bad} skipped)")


def verify_casemeta(run_id: str | None = None):
    """Checks for `aidag verify casemeta`. Run-independent."""
    valid = set(pl.read_parquet(PROCESSED_DIR / "cases.parquet")["votering_id"].to_list())
    recs = load_casemeta()
    bad = unknown = 0
    for vid, rec in recs.items():
        if vid not in valid:
            unknown += 1
            continue
        try:
            validate_record(rec)
        except Exception:  # noqa: BLE001
            bad += 1
    yield ("casemeta records valid + party-blind", bad == 0, f"{len(recs)} records, {bad} invalid/leaky")
    yield ("every casemeta id is a real votering", unknown == 0, f"{unknown} unknown ids")
