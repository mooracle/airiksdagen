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
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _reservation_summaries() -> dict[str, str]:
    """`votering_id:alt_id` -> the reservation's demand, in Swedish.

    The reservations layer's own parse succeeds on betänkanden where
    `parse_reservations_indexed` returns nothing, so it is the grounding source of
    last resort in `build_packet`. Already scrubbed and party-blind.
    """
    from aidag.reservations import load_reservations

    out: dict[str, str] = {}
    for key, rec in load_reservations().items():
        subj = rec.get("subject") or {}
        if sv := (subj.get("sv") if isinstance(subj, dict) else subj):
            out[key] = str(sv).strip()
    return out


def _reservation_summary(votering_id: str, alt_id: str) -> str | None:
    return _reservation_summaries().get(f"{votering_id}:{alt_id}")


def _reservations_of(case: dict) -> list[dict]:
    alts = case["alternatives"]
    if isinstance(alts, str):
        alts = json.loads(alts)
    return [a for a in alts if a.get("alt_id") != "utskottet"]


# A motion's demand opens with "I motion <nr>" / "I kommittémotion <nr>" in the
# betänkande's own "Motionerna" prose, and runs to the next such opening or to the
# committee's answer.
_MOTION_OPEN = r"I (?:kommitté|parti|flerparti|enskild)?motion(?:erna)?\s+"
_MOTION_END = re.compile(
    r"Utskottets ställningstagande|" + _MOTION_OPEN + r"\d{4}/\d{2}:\d+|"
    r"Reservation(?:er)?\b|Särskilt yttrande|Bilaga\b"
)

# The two appendices a simplified-handling punkt needs. Bilaga 2 maps punkt ->
# (motion, yrkanden); Bilaga 1 carries every yrkande's verbatim demand. Both
# headings also appear in the document's own table of contents, so callers take
# the LAST occurrence — the appendix itself.
_BILAGA1 = "Bilaga 1 Förteckning över behandlade förslag"
_BILAGA2 = "Bilaga 2 Motionsyrkanden som avstyrks av utskottet"


def _norm(plain: str) -> str:
    """Plain fulltext with the double-escaped entities these documents carry
    (`&#xa0;`, `&#xad;`) collapsed to spaces, so a token match is not defeated by
    a soft hyphen sitting inside a word."""
    return re.sub(r"\s+", " ", re.sub(r"&#x[0-9a-f]+;|&nbsp;", " ", plain)).strip()


def bundle_yrkanden(case: dict, plain: str) -> list[dict]:
    """Every motion yrkande a "Motioner som bereds förenklat" punkt turns down.

    These punkter name no motion in `forslag_text` — they reject a bundle listed
    in "utskottets förteckning över avstyrkta motionsyrkanden" — so
    `motion_demands` finds nothing and the punkt has no Nej side. The bundle IS
    fully recoverable: Bilaga 2 lists this punkt's motions and yrkande numbers,
    and Bilaga 1 states each one verbatim (100% coverage across the three
    full-v4 cases: 18, 39 and 23 motions).

    Recovering it does NOT make the punkt decidable under p6, and that is the
    point of having this function. The bundle is grouped by topic, not by
    direction, so it routinely demands a thing and its opposite in one vote:
    UU15 punkt 7 asks both that UNRWA be wound up (three motions) and that
    support to Palestine including UNRWA continue and be developed (one), and
    both that Sweden revoke its recognition of Palestine and that it impose a
    military embargo on Israel. `hallning` is one field answering "does the
    party's plan support what the counter-proposal demands in substance", and
    against a self-contradictory bundle every party's plan supports some of it
    and rejects the rest — so there is no answer to give rather than a missing
    one. This turns that from an assumption into something a reader can check.
    """
    text = _norm(plain)
    try:
        b1 = text.rindex(_BILAGA1)
        b2 = text.rindex(_BILAGA2)
    except ValueError:
        return []
    listing = text[b2:]
    m = re.search(
        rf"\b{case['punkt']}\.\s+Motioner som bereds förenklat(?P<body>.*?)"
        r"(?=\b\d+\.\s+[A-ZÅÄÖ]|$)",
        listing,
        re.S,
    )
    if not m:
        return []
    catalogue = text[b1:b2]
    out = []
    for nr in sorted(set(re.findall(r"\d{4}/\d{2}:\d+", m.group("body")))):
        i = catalogue.find(nr)
        if i < 0:
            continue
        nxt = re.search(r"\d{4}/\d{2}:\d+", catalogue[i + len(nr) :])
        end = i + len(nr) + (nxt.start() if nxt else len(catalogue))
        out.append({"motion": nr, "text": catalogue[i:end].strip()})
    return out


def motion_demands(case: dict, plain: str) -> list[dict]:
    """The Nej side for a punkt that has no reservation.

    On these points the counter-proposal is a MOTION the committee proposes to
    reject, not a reservation — so `_reservations_of` comes back empty, the Nej
    side used to come out blank, and the p6 stance was left with no referent at
    all (`promptgen.p6_decidable` has the mechanism and what it cost on full-v4).
    The betänkande's own "Motionerna" prose states each motion's demand; slicing
    it is what recovers them, with no model in the path.

    A motion id is one the fulltext introduces with "I motion <nr>". That test —
    rather than reading the ids out of `forslag_text` directly — is what separates
    motions from the propositions and skrivelser named in the SAME sentence
    ("Därmed bifaller riksdagen proposition 2025/26:254 … och avslår motion
    2025/26:4176", "lägger skrivelse 2021/22:265 till handlingarna"). Those are
    what the committee is FOR; taking one for the Nej side would invert the case.

    Returns [] when the point has no single demand to state — which is the honest
    answer for "Motioner som bereds förenklat", where the committee rejects a
    bundle of 17-20 unrelated yrkanden in one go and `forslag_text` names no
    motion at all. Those points stay undecidable under p6 by design.
    """
    out = []
    for nr in sorted(set(re.findall(r"\d{4}/\d{2}:\d+", case.get("forslag_text") or ""))):
        m = re.search(_MOTION_OPEN + re.escape(nr) + r"\b", plain)
        if not m:
            continue  # a proposition or skrivelse, not a motion
        rest = plain[m.end() :]
        end = _MOTION_END.search(rest)
        body = (plain[m.start() : m.end() + (end.start() if end else len(rest))]).strip()
        # These fulltexts are double-escaped, so `_plain`'s single unescape leaves
        # literal "&#xa0;" / "&#xad;" in the prose. Harmless in a parse, noise in a
        # prompt — the p6 <arende> is built from this text.
        body = re.sub(r"&#x[0-9a-f]+;|&nbsp;", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        out.append({"alt_id": f"mot-{nr.split(':')[1]}", "motion": nr, "body": body[:2200]})
    return out


def build_packet(
    case: dict, blocks: list[dict], entries: list[dict], plain: str = ""
) -> dict:
    """Deterministic per-case source packet: parquet facts + Ja candidates + Nej bodies,
    in a party-aware `display_src` and a scrubbed `agent_src`. `flags` records association
    cross-checks (missing/party mismatch) for the validator to gate on.

    `plain` is the betänkande fulltext as plain prose, used only to recover the Nej
    side on a punkt with no reservation (see `motion_demands`). Defaults to empty so
    existing callers keep the reservation-only behaviour."""
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
            # `parse_reservations_indexed` is a second, narrower parser than the one
            # behind the reservations layer, and on some betänkanden (notably
            # single-reservation ones headed without an explicit punkt) it finds
            # nothing at all. That used to produce a silently EMPTY Nej side: the
            # committee candidates still parsed, so `fallback` stayed False and no
            # instruction covered the case. 42 votes were affected — every one of
            # them with a real counter-proposal, and 31 with an already-summarized
            # reservation sitting in the reservations layer.
            #
            # Fall back to that summary. It is derived from the same fulltext, is
            # already scrubbed and party-blind (`verify reservations` gates it), and
            # is strictly better grounding than nothing. Flagged so the substitution
            # stays visible rather than silently changing what the agent read.
            if sub := _reservation_summary(case["votering_id"], a["alt_id"]):
                flags.append(f"{a['alt_id']}: grounded on reservations-layer summary")
                nej_display.append({"alt_id": a["alt_id"],
                                    "party": a.get("source_partier") or [], "body": sub})
                nej_agent.append({"alt_id": a["alt_id"], "body": sub})
            else:
                flags.append(f"{a['alt_id']}: no entry on punkt {case['punkt']}")
            continue
        if a.get("source_partier") and not (set(e["party"]) & set(a["source_partier"])):
            flags.append(f"{a['alt_id']}: party {e['party']}!={a['source_partier']}")
        nej_display.append({"alt_id": a["alt_id"], "party": e["party"], "body": e["body"][:2200]})
        nej_agent.append({"alt_id": a["alt_id"], "body": scrub_substance(e["body"])[:2200]})

    # No reservation on this punkt: the counter-proposal is a motion the committee
    # proposes to reject. Recovering it is what makes the point decidable under p6
    # at all — an empty Nej side here does not mean "uncontested", it means the
    # stance field has nothing to refer to. Only consulted when the reservation
    # path found nothing, so no existing packet changes.
    if not nej_display and (demands := motion_demands(case, plain)):
        for d in demands:
            flags.append(f"{d['alt_id']}: Nej side recovered from motion {d['motion']}")
            nej_display.append({"alt_id": d["alt_id"], "party": [], "body": d["body"]})
            nej_agent.append({"alt_id": d["alt_id"], "body": scrub_substance(d["body"])[:2200]})

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
        plain = _plain(full)
        for row in rows:
            packets.append(build_packet(row, blocks, resindex, plain))
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
    "committee's position + main reason; nej[] = each COUNTER-PROPOSAL's demand + reason "
    "(copy alt_id — `res-N` is a reservation, `mot-N` a motion the committee turns down; "
    "state the demand either way); agent.committee + agent.alternatives[] = the same Ja/Nej substance but "
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


def prepare(
    batch_size: int = 500, per_request: int = 6, only: list[str] | None = None
) -> None:
    """Emit the next casemeta batch manifest from pending cases. Each request file bundles
    per_request case packets; one Sonnet agent per file writes {cases:[record,...]}.

    `only` re-issues named cases that are already built — for a source-side repair,
    where the packet changed but the id is no longer pending. `ingest` dedupes on
    votering_id, so the new record replaces the old one. Every id must exist and
    already be built: a typo would otherwise print the same output as a valid
    re-issue while quietly rebuilding nothing.
    """
    if only:
        built = set(load_casemeta())
        if unknown := [v for v in only if v not in built]:
            raise ValueError(f"--only names {len(unknown)} case(s) not already built: {unknown}")
        pending = list(only)
    else:
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


UNDECIDABLE_PATH = CASEMETA_DIR / "p6-undecidable.json"


def undecidable_report(write: bool = False) -> dict:
    """Which cases p6 cannot decide, and — for each — the source that proves it.

    A p6 case needs a counter-proposal for `hallning` to refer to. Two reasons a
    case can lack one, and they are not the same finding:

      `no_nej_side`   — the punkt has neither a reservation nor a recoverable
                        motion demand. A gap; re-check when extraction improves.
      `contradictory_bundle` — "Motioner som bereds förenklat". The demands ARE
                        fully recoverable (`bundle_yrkanden`), and recovering them
                        is what shows the punkt has no single demand to take a
                        stance on: the bundle is grouped by topic, so it asks for a
                        thing and its opposite in one vote. Not a gap, and not
                        fixable by better parsing.

    Writing the report is what keeps the exclusion auditable instead of a silent
    hole: the recovered yrkanden are the evidence, so a reader can check the claim
    and a later extraction change cannot quietly turn one class into the other.
    """
    from aidag.promptgen import p6_decidable

    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    rows, by_dok = [], {}
    for c in cases.iter_rows(named=True):
        if not p6_decidable(c, "anonymous"):
            by_dok.setdefault(c["dok_id"], []).append(c)
    for dok, members in sorted(by_dok.items()):
        plain = _plain(fetch_fulltext(dok))
        for c in members:
            ys = bundle_yrkanden(c, plain)
            rows.append({
                "votering_id": c["votering_id"],
                "beteckning": c["beteckning"],
                "punkt": c["punkt"],
                "rubrik": c["rubrik"],
                "reason": "contradictory_bundle" if ys else "no_nej_side",
                "n_motions": len(ys),
                "yrkanden": ys,
            })
    rows.sort(key=lambda r: (r["reason"], r["beteckning"], r["punkt"]))
    report = {"n_undecidable": len(rows), "cases": rows}
    for r in rows:
        print(f"  {r['reason']:22s} {r['beteckning']:8s} punkt {r['punkt']:2d}  "
              f"{r['n_motions']:3d} motions  {r['rubrik'][:44]}")
    print(f"{len(rows)} cases undecidable under p6")
    if write:
        UNDECIDABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UNDECIDABLE_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(f"wrote {UNDECIDABLE_PATH}")
    return report


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


def ingest(input_path: str, model: str, replace: bool = False) -> None:
    """Ingest workflow output ({cases:[record,...]} files, or a dir of them). Validates +
    de-leaks the agent view, merges deterministic fields, dedupes on votering_id.

    Append-only and FIRST-wins by default: an id already in `cases.jsonl` is skipped,
    so re-ingesting a directory cannot corrupt records that are already good.

    `replace` is the counterpart to `prepare(only=...)`. A source-side repair re-issues
    ids that are already built, and without this they would all be skipped — the pass
    would print "ingested 0" and look like a no-op that succeeded. Replacing rewrites
    the file rather than appending, so it is deliberately opt-in.
    """
    from pathlib import Path

    from aidag.metadata import extract_deterministic

    p = Path(input_path)
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    records = [r for f in files for r in json.loads(f.read_text()).get("cases", [])]
    cases = {c["votering_id"]: c for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)}
    existing = load_casemeta()
    done = set(existing)
    now = datetime.now(timezone.utc).isoformat()
    n_ok = n_bad = n_replaced = 0
    accepted: dict[str, dict] = {}
    for rec in records:
        vid = rec.get("votering_id")
        if not vid:
            continue
        if vid in done and not replace:
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
        if vid in done:
            n_replaced += 1
        done.add(vid)
        accepted[vid] = {
            "votering_id": vid,
            **extract_deterministic(cases[vid]),
            **{k: rec[k] for k in ("subject", "subtopics", "decision", "at_stake", "ja", "nej", "agent")},
            "model": model,
            "collected_at": now,
        }
        n_ok += 1
    cases_path().parent.mkdir(parents=True, exist_ok=True)
    if replace:
        # Rewrite in place, preserving the original line order so a repair shows up
        # as a diff on the repaired records only.
        merged = {**existing, **accepted}
        cases_path().write_text(
            "".join(json.dumps(merged[v], ensure_ascii=False) + "\n" for v in merged)
        )
    else:
        with open(cases_path(), "a") as f:
            for rec in accepted.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"ingested {n_ok} casemeta records ({n_bad} skipped, {n_replaced} replaced)")


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

    # Completeness, not just validity. A record can be perfectly valid and still
    # carry an EMPTY Nej side, which is invisible here but silently degrades the
    # p6 agent prompt: `promptgen._render_p6_arende` returns None when
    # `agent.alternatives` is empty and falls back to the p5 block — a ~92-char
    # hollow Ja-only brief.
    #
    # This check is scoped to cases whose source record lists reservations. It used
    # to say the fallback "is correct for a votering with no counter-proposal", and
    # that was wrong in a way that cost 80 published decisions: correct for p5, which
    # asks for a vote and gets one, but NOT for p6, where the fallback also swaps the
    # closing question while the schema still only accepts a stance. The p6 half of
    # the guard is `promptgen.p6_decidable`, enforced in `simulate.verify_run` and
    # `agent_run.prepare`; a punkt with no reservation but a rejected motion is
    # recovered by `motion_demands` rather than left to the fallback.
    cases = {
        c["votering_id"]: c
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
    }
    degraded = []
    for vid, case in cases.items():
        # `_reservations_of` is the right filter, not "alternatives is non-empty":
        # a votering whose only alternative is `utskottet` has no counter-proposal
        # at all, so an empty Nej side is correct and the p5 fallback is right.
        if not _reservations_of(case):
            continue
        ag = (recs.get(vid) or {}).get("agent") or {}
        if not [a for a in (ag.get("alternatives") or []) if (a.get("sv") or "").strip()]:
            degraded.append(vid)
    yield (
        "contested cases have a Nej side (agent.alternatives)",
        not degraded,
        f"{len(degraded)} contested cases would fall back to the p5 hollow brief",
    )
