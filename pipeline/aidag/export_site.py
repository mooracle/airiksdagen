"""Export cases, votes, decisions and aggregates as JSON for the Astro build.

Writes site/src/data/ (committed — regenerated locally by `aidag export-site`):
  cases/{votering_id}.json   full case + per-seat votes + 8 AI decisions
  index/cases-index.json     one compact row per case for the search island
  aggregates/*.json          copied from results/aggregates/{run_id}/
  meta.json                  run metadata, party table, attribution

The AI-side aggregates (`ai_pairs_matrix`, `by_area`, `flips`) are computed here
rather than in `aggregate.py`: they need decisions, real seat counts AND case
metadata joined per division, and this is the only place all three are already
in memory. The maths lives in `aivotes.py`; this module only feeds it.
"""

from __future__ import annotations

import json
import shutil

import polars as pl

from aidag import aivotes, coalition
from aidag.promptgen import evidence_tier
from aidag.config import (
    HEMICYCLE_ORDER,
    PARTIES,
    PARTY_PROGRAMS,
    PROCESSED_DIR,
    RESULTS_DIR,
    RIKSDAG_ATTRIBUTION,
    SITE_DATA_DIR,
)


def export_corpus() -> None:
    """Write the corpus documents the /dokument/ pages render.

    Serves `corpus.normalize()` output, NOT the raw file. The page states that this
    is what the AI agents read, and that is only true of the normalized text:
    `documents_for()` routes every prompt through `normalize()`, so the raw file
    still carries the artifacts it strips — the f-ligatures, the soft hyphens, the
    page furniture, and mp-2013's undecodable display-font headings, one of which
    sits mid-sentence and split a real citation in two.

    Consequence for citation deep-links: quotes verify against the NORMALIZED text
    (that is what `repair-citations` and `verify simulate` compare to), so serving
    it is also what makes every `#:~:text=` fragment resolvable.

    The provenance comment is re-attached because `normalize()` strips it and the
    page renders it as a source credit.
    """
    import re

    from aidag.config import CORPUS_DIR
    from aidag.corpus import normalize

    corpus_out = SITE_DATA_DIR / "corpus"
    corpus_out.mkdir(parents=True, exist_ok=True)
    for txt in sorted(CORPUS_DIR.glob("*.txt")):
        raw = txt.read_text(encoding="utf-8").lstrip("﻿")
        header = re.match(r"^<!--.*?-->\n", raw, flags=re.S)
        body = normalize(raw)
        (corpus_out / txt.name).write_text(
            (header.group(0) if header else "") + body + "\n", encoding="utf-8"
        )


def merge_case_metadata(payload: dict, index_row: dict, meta_rec: dict | None) -> None:
    """Merge a case-metadata record into the per-case payload and the lean index row.

    Hermetic (mutates the two dicts, no I/O) so it is unit-testable without the
    monolithic run(). The per-case JSON gets the full display metadata under a
    namespaced `meta`; the client-fetched index row gets ONLY the filter keys
    (`policy_area` code + `type`) and a lowercased `search` blob holding just the
    NEW searchable text — `subject.sv + subject.en + subtopics`. It deliberately
    does NOT duplicate rubrik/rubrik_en/titel/bet (already on the row — the client
    ORs those in) and never carries the full subject fields or `at_stake`. No-op
    when `meta_rec` is None: the page falls back to `sammanfattning` (utsknotis)
    and the row keeps only its display fields for search.

    `decision`/`ja`/`nej` are the casemeta brief — the contested axis, the committee
    position, and each reservation's demand. Display-side only: they stay off the
    index row, which must remain lean. `agent.*` is deliberately NOT exported here;
    it is the party-blind prompt slice, not a site field.
    """
    if not meta_rec:
        return
    subtopics = meta_rec.get("subtopics") or []
    payload["meta"] = {
        "type": meta_rec.get("type"),
        "policy_area": meta_rec.get("policy_area"),
        "subject": meta_rec.get("subject"),
        "at_stake": meta_rec.get("at_stake"),
        "subtopics": subtopics,
        "parties_involved": meta_rec.get("parties_involved") or [],
        # casemeta brief; absent on pre-casemeta records, so keep each optional
        "decision": meta_rec.get("decision"),
        "ja": meta_rec.get("ja"),
        "nej": meta_rec.get("nej") or [],
    }
    index_row["policy_area"] = meta_rec.get("policy_area")
    index_row["type"] = meta_rec.get("type")
    subject = meta_rec.get("subject") or {}
    index_row["search"] = " ".join(
        s for s in (subject.get("sv") or "", subject.get("en") or "", " ".join(subtopics)) if s
    ).lower()


def load_decisions_by_case(run_id: str | None) -> dict[str, dict[str, dict]]:
    if run_id is None:
        return {}
    from aidag.translate import load_decision_translations

    translations = load_decision_translations(run_id)
    out: dict[str, dict[str, dict]] = {}
    sim_dir = RESULTS_DIR / "simulations" / run_id
    for path in sorted(sim_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("arm", "anonymous") != "anonymous":
                continue  # the anonymous arm is the headline result
            cid = f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"
            tr = translations.get(cid)
            out.setdefault(d["votering_id"], {})[d["parti"]] = {
                "rost": d["rost"],
                # p6: the plan's stance on what the counter-proposal demands, and
                # how far the plan actually reached this vote. `rost` above is
                # DERIVED from `hallning`, so on a p6 run the stance is the
                # primary field and the vote is its consequence. Both None on
                # p4/p5 runs, where the agent supplied `rost` directly.
                "hallning": d.get("hallning"),
                "plan_tacker_utskottets_skal": d.get("plan_tacker_utskottets_skal"),
                # explicit | extrapolated | off_axis — only `explicit` can carry a
                # "the party voted against its own stated commitment" claim.
                "tier": evidence_tier(d) if d.get("hallning") else None,
                "confidence": d["confidence"],
                "coverage": d["coverage"],
                "motivering": d["motivering"],
                "citations": d["citations"],
                "omvarld": d.get("omvarld") or {"paverkar": False, "faktorer": []},
                "flags": d["flags"],
                "model": d["model"],
                # English translation of motivering/quotes/princip/omvärld
                # (null until the translation batch for this decision has run)
                "en": (
                    {k: tr[k] for k in ("motivering", "citations", "omvarld")} if tr else None
                ),
            }
    return out


def seat_array(votes: pl.DataFrame) -> list[list]:
    """Per-seat [parti, rost] ordered for the hemicycle: party blocks in
    left-right order, MPs alphabetically within party."""
    order = {p: i for i, p in enumerate(HEMICYCLE_ORDER)}
    rows = votes.sort(
        pl.col("parti").replace_strict(order, default=99), "namn"
    ).select("parti", "rost", "namn", "valkrets")
    return [[r["parti"], r["rost"], r["namn"], r["valkrets"]] for r in rows.iter_rows(named=True)]


def annotate_coalition(alternatives: list[dict], actual: dict, ai: dict) -> None:
    """Tag each party with the procedural fact and the programme/coalition conflict.

    actual[p].authored_reservation  did the party move the reservation itself
    ai[p].program_override          'strict'|'loose' when the party's own documents
                                    explicitly implied opposing the committee and it
                                    voted Ja anyway (see coalition.py for the caveat)
    """
    authors = {
        p
        for alt in alternatives
        if alt.get("alt_id") != "utskottet"
        for p in (alt.get("source_partier") or [])
    }
    for p, a in actual.items():
        a["authored_reservation"] = p in authors
    for p, d in ai.items():
        overridden = (
            d.get("rost") in coalition.DIVERGENT
            and d.get("coverage") == "explicit"
            and actual.get(p, {}).get("position") == "Ja"
        )
        d["program_override"] = (
            ("strict" if d.get("confidence") == "high" else "loose") if overridden else None
        )


def run(run_id: str | None = None) -> None:
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    votes = (
        pl.read_parquet(PROCESSED_DIR / "votes.parquet")
        .filter(pl.col("avser") == "sakfrågan", pl.col("votering") == "huvud")
        .with_columns(pl.col("parti").str.to_uppercase())
        .filter(pl.col("parti").is_in(list(PARTIES)))
    )
    decisions = load_decisions_by_case(run_id)
    from aidag.translate import load_case_translations

    case_translations = load_case_translations()
    # casemeta supersedes the older `metadata` layer: same per-vote keys plus the
    # brief (decision / ja / nej) and a populated party-blind `agent.*`.
    from aidag.casemeta import load_casemeta

    metadata_by_vid = load_casemeta()
    from aidag.reservations import load_reservations

    reservations_by_key = load_reservations()  # keyed 'votering_id:alt_id'

    cases_dir = SITE_DATA_DIR / "cases"
    if cases_dir.exists():
        shutil.rmtree(cases_dir)
    cases_dir.mkdir(parents=True)
    (SITE_DATA_DIR / "index").mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / "aggregates").mkdir(parents=True, exist_ok=True)

    pos_by_case: dict[str, dict] = {}
    for r in positions.iter_rows(named=True):
        pos_by_case.setdefault(r["votering_id"], {})[r["parti"]] = {
            "position": r["position"],
            "n_ja": r["n_ja"],
            "n_nej": r["n_nej"],
            "n_avstar": r["n_avstar"],
            "n_franvarande": r["n_franvarande"],
            "cohesion": round(r["cohesion"], 3),
        }

    index = []
    # one row per division, fed to aivotes after the loop — collected here so the
    # AI aggregates see exactly the corpus that was exported, nothing else
    ai_rows: list[dict] = []
    for case in cases.sort("datum", "votering_id").iter_rows(named=True):
        vid = case["votering_id"]
        case_votes = votes.filter(pl.col("votering_id") == vid)
        case_decisions = decisions.get(vid, {})
        actual = pos_by_case.get(vid, {})
        n_agree = sum(
            1 for p, d in case_decisions.items()
            if actual.get(p, {}).get("position") == d["rost"]
        )
        n_compared = sum(
            1 for p in case_decisions
            if actual.get(p, {}).get("position") not in (None, "Frånvarande")
        )
        alternatives = json.loads(case["alternatives"]) if isinstance(case["alternatives"], str) else case["alternatives"]
        # attach recovered reservation substance (party-blind {sv,en}) so the site
        # can show what each Nej alternative proposes instead of just "Reservation N"
        for a in alternatives:
            r = reservations_by_key.get(f"{vid}:{a.get('alt_id')}")
            if r:
                a["substance"] = r["subject"]
        from aidag.compact import compact_meanings

        annotate_coalition(alternatives, actual, case_decisions)

        tr = case_translations.get(vid)
        payload = {
            "compact": compact_meanings(case["forslag_text"], alternatives),
            # English case texts; null until translated (site falls back to Swedish)
            "en": (
                {k: tr[k] for k in ("rubrik", "dok_titel", "forslag_text", "notis", "alternatives")}
                if tr
                else None
            ),
            "votering_id": vid,
            "rm": case["rm"],
            "beteckning": case["beteckning"],
            "punkt": case["punkt"],
            "dok_id": case["dok_id"],
            "datum": case["datum"],
            "utskott": case["utskott"],
            "rubrik": case["rubrik"],
            "dok_titel": case["dok_titel"],
            # utsknotis = committee's PRE-decision summary (what the agents read);
            # surfaced as the case page's lead. notis (post-decision) stays at the bottom.
            "sammanfattning": case["utsknotis"],
            "forslag_text": case["forslag_text"],
            "notis": case["notis"],
            "alternatives": alternatives,
            "actual": actual,
            "ai": case_decisions,
            "seats": seat_array(case_votes),
            "references": json.loads(case["references"]) if isinstance(case.get("references"), str) else (case.get("references") or []),
            "source_url": f"https://data.riksdagen.se/dokumentstatus/{case['dok_id']}.json",
            "fulltext_url": f"https://data.riksdagen.se/dokument/{case['dok_id']}.html",
            "votering_url": f"https://data.riksdagen.se/votering/{vid}/json",
            "riksdagen_url": f"https://www.riksdagen.se/sv/dokument-och-lagar/dokument/betankande/_{case['dok_id']}/",
        }
        # parties whose AI vote differed from the actual (compared) vote,
        # ordered left→right on the hemicycle so the badges read spatially
        miss = sorted(
            (
                p for p, d in case_decisions.items()
                if actual.get(p, {}).get("position") not in (None, "Frånvarande")
                and actual.get(p, {}).get("position") != d["rost"]
            ),
            key=lambda p: HEMICYCLE_ORDER.index(p) if p in HEMICYCLE_ORDER else 99,
        )
        entry = {
            "id": vid,
            "datum": case["datum"],
            "rm": case["rm"],
            "bet": f"{case['rm']}:{case['beteckning']}",
            "punkt": case["punkt"],
            "utskott": case["utskott"],
            "rubrik": case["rubrik"],
            "rubrik_en": tr["rubrik"] if tr else None,
            "titel": case["dok_titel"],
            "agree": n_agree,
            "compared": n_compared,
            "hasAi": bool(case_decisions),
        }
        if miss:  # omitted when empty to keep the client index lean
            entry["miss"] = miss
        # The subset of `miss` that can carry a documented-commitment claim: the
        # plan stated it outright (evidence tier `explicit`) and the party still
        # voted the other way. Real abstentions are excluded — an abstention is a
        # floor tactic the plan was never asked to express. Empty on p4/p5 runs.
        missx = [
            p for p in miss
            if case_decisions[p].get("tier") == "explicit"
            and actual.get(p, {}).get("position") in ("Ja", "Nej")
        ]
        if missx:
            entry["missx"] = missx
        # The same set re-run as a division: `missx` is exactly the parties
        # aivotes.flip() is allowed to move, so `flip.parties` == `missx` and the
        # payload carries only what that changes — the two seat counts and
        # whether the chamber's answer survives. None when missx is empty.
        flip = aivotes.flip(actual, case_decisions)
        if flip:
            payload["flip"] = flip
            if flip["flips"]:
                # 1, not the party list: `missx` is already on the row and the
                # browser only needs the flag to filter and mark on
                entry["flip"] = 1
        # merge case metadata into BOTH the full payload and the lean index row,
        # then write the payload (after the merge, so `meta` is included)
        merge_case_metadata(payload, entry, metadata_by_vid.get(vid))
        (cases_dir / f"{vid}.json").write_text(json.dumps(payload, ensure_ascii=False))
        index.append(entry)
        ai_rows.append({
            "votering_id": vid,
            "rm": case["rm"],
            "datum": case["datum"],
            "utskott": case["utskott"],
            "rubrik": case["rubrik"],
            # merge_case_metadata has run, so this is the code it just wrote
            "policy_area": entry.get("policy_area"),
            "actual": actual,
            "ai": case_decisions,
            "flip": flip,
        })

    index_json = json.dumps(index, ensure_ascii=False)
    (SITE_DATA_DIR / "index" / "cases-index.json").write_text(index_json)
    # The case browser fetches the index client-side -> must live in public/.
    public_data = SITE_DATA_DIR.parents[1] / "public" / "data"
    public_data.mkdir(parents=True, exist_ok=True)
    (public_data / "cases-index.json").write_text(index_json)

    agg_src = RESULTS_DIR / "aggregates" / run_id if run_id else None
    if agg_src and agg_src.exists():
        for f in agg_src.glob("*.json"):
            shutil.copy(f, SITE_DATA_DIR / "aggregates" / f.name)

    # Corpus documents for the /dokument/ pages (citation deep links).
    export_corpus()

    # Party polling support per month, from the KB snapshots (run-independent).
    from aidag.config import KB_DIR

    support_rows = []
    for snap_path in sorted(KB_DIR.glob("*.json")):
        snap = json.loads(snap_path.read_text())
        support = snap.get("party_support")
        if support:
            for parti, value in support["parties"].items():
                support_rows.append({
                    "month": snap["month"],
                    "parti": parti,
                    "support": value,
                    "n_polls": support["n_polls"],
                })
    (SITE_DATA_DIR / "aggregates" / "party_support.json").write_text(
        json.dumps(support_rows, ensure_ascii=False)
    )

    # Real-vote analytics (no AI involved) + researcher downloads.
    from aidag import analytics

    agg_dir = SITE_DATA_DIR / "aggregates"
    (agg_dir / "pairs_matrix.json").write_text(json.dumps(analytics.pairs_matrix()))
    (agg_dir / "gov_defeats.json").write_text(
        json.dumps(analytics.gov_defeats(), ensure_ascii=False)
    )
    (agg_dir / "cohesion.json").write_text(json.dumps(analytics.cohesion_series()))
    (agg_dir / "dissenters.json").write_text(
        json.dumps(analytics.dissenter_league(), ensure_ascii=False)
    )

    # AI-side analytics — the same chamber read off the parties' own documents.
    # Skipped entirely when the run has no decisions, so an AI-less export does
    # not leave three empty files behind claiming otherwise.
    if any(r["ai"] for r in ai_rows):
        (agg_dir / "ai_pairs_matrix.json").write_text(
            json.dumps(
                aivotes.pairs_matrix([
                    {"rm": r["rm"], "ai": {p: d["rost"] for p, d in r["ai"].items()}}
                    for r in ai_rows
                    if r["ai"]
                ])
            )
        )
        (agg_dir / "by_area.json").write_text(
            json.dumps(aivotes.area_stats(ai_rows), ensure_ascii=False)
        )
        (agg_dir / "flips.json").write_text(
            json.dumps(aivotes.flip_summary(ai_rows), ensure_ascii=False)
        )

    import gzip

    downloads = SITE_DATA_DIR.parents[1] / "public" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    positions.write_csv(downloads / "party_positions.csv")
    cases.drop("alternatives", "references").write_csv(downloads / "cases.csv")
    # Open-data case-metadata export: full casemeta records (incl. at_stake, the
    # decision/ja/nej brief and the de-leaked agent view), one JSONL row per
    # exported case that has a record.
    with open(downloads / "case-metadata.jsonl", "w") as f:
        for row in index:
            rec = metadata_by_vid.get(row["id"])
            if rec:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if run_id:
        sim_dir = RESULTS_DIR / "simulations" / run_id
        lines = []
        for f in sorted(sim_dir.glob("*.jsonl")):
            lines.append(f.read_text())
        # mtime=0: gzip stamps the current time into the header by default, so an
        # unchanged simulation still produced a byte-different 1 MB blob on every
        # export. Pinning it keeps the committed artifact reproducible.
        with gzip.GzipFile(downloads / f"decisions-{run_id}.jsonl.gz", "wb", mtime=0) as gz:
            gz.write("".join(lines).encode())

    from aidag.metadata import policy_area_labels

    meta = {
        "run_id": run_id,
        "n_cases": len(index),
        "parties": {
            code: {**info, "seats_order": HEMICYCLE_ORDER.index(code)}
            for code, info in PARTIES.items()
        },
        "hemicycle_order": HEMICYCLE_ORDER,
        # policy_area code -> {sv, en} localized filter/chip labels (run-independent)
        "policy_areas": policy_area_labels(),
        # Date-gated party-programme versions, oldest-first, mirroring
        # PARTY_PROGRAMS. A citation records only document="partiprogram", and
        # most parties have replaced theirs mid-term, so the site cannot tell
        # which file a quote came from without the adoption dates: the applicable
        # version is the last one whose `from` <= the vote date, the same rule the
        # agent's context was built with. Without this the site simply dropped the
        # link, leaving partiprogram quotes unverifiable.
        "party_programs": {
            code: [
                {"slug": f"partiprogram-{code.lower()}-{v['from'][:4]}", "from": v["from"]}
                for v in versions
            ]
            for code, versions in PARTY_PROGRAMS.items()
        },
        "attribution": RIKSDAG_ATTRIBUTION,
    }
    (SITE_DATA_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"exported {len(index)} cases to {SITE_DATA_DIR} (run_id={run_id})")
