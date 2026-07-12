"""Export cases, votes, decisions and aggregates as JSON for the Astro build.

Writes site/src/data/ (gitignored — regenerated in CI):
  cases/{votering_id}.json   full case + per-seat votes + 8 AI decisions
  index/cases-index.json     one compact row per case for the search island
  aggregates/*.json          copied from results/aggregates/{run_id}/
  meta.json                  run metadata, party table, attribution
"""

from __future__ import annotations

import json
import shutil

import polars as pl

from aidag import coalition
from aidag.config import (
    HEMICYCLE_ORDER,
    PARTIES,
    PROCESSED_DIR,
    RESULTS_DIR,
    RIKSDAG_ATTRIBUTION,
    SITE_DATA_DIR,
)


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


def load_probes(run_id: str | None) -> dict[str, dict]:
    if run_id is None:
        return {}
    path = RESULTS_DIR / "probes" / run_id / "probe.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            p = json.loads(line)
            out[p["votering_id"]] = {
                "recalls_case": p["recalls_case"],
                "exact_match_count": p["exact_match_count"],
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
    probes = load_probes(run_id)
    from aidag.translate import load_case_translations

    case_translations = load_case_translations()

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
            "forslag_text": case["forslag_text"],
            "notis": case["notis"],
            "alternatives": alternatives,
            "actual": actual,
            "ai": case_decisions,
            "probe": probes.get(vid),
            "seats": seat_array(case_votes),
            "references": json.loads(case["references"]) if isinstance(case.get("references"), str) else (case.get("references") or []),
            "source_url": f"https://data.riksdagen.se/dokumentstatus/{case['dok_id']}.json",
            "fulltext_url": f"https://data.riksdagen.se/dokument/{case['dok_id']}.html",
            "votering_url": f"https://data.riksdagen.se/votering/{vid}/json",
            "riksdagen_url": f"https://www.riksdagen.se/sv/dokument-och-lagar/dokument/betankande/_{case['dok_id']}/",
        }
        (cases_dir / f"{vid}.json").write_text(json.dumps(payload, ensure_ascii=False))
        index.append({
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
    from aidag.config import CORPUS_DIR

    corpus_out = SITE_DATA_DIR / "corpus"
    corpus_out.mkdir(parents=True, exist_ok=True)
    for txt in CORPUS_DIR.glob("*.txt"):
        shutil.copy(txt, corpus_out / txt.name)

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

    import gzip

    downloads = SITE_DATA_DIR.parents[1] / "public" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    positions.write_csv(downloads / "party_positions.csv")
    cases.drop("alternatives", "references").write_csv(downloads / "cases.csv")
    if run_id:
        sim_dir = RESULTS_DIR / "simulations" / run_id
        lines = []
        for f in sorted(sim_dir.glob("*.jsonl")):
            lines.append(f.read_text())
        with gzip.open(downloads / f"decisions-{run_id}.jsonl.gz", "wt") as gz:
            gz.write("".join(lines))

    meta = {
        "run_id": run_id,
        "n_cases": len(index),
        "parties": {
            code: {**info, "seats_order": HEMICYCLE_ORDER.index(code)}
            for code, info in PARTIES.items()
        },
        "hemicycle_order": HEMICYCLE_ORDER,
        "attribution": RIKSDAG_ATTRIBUTION,
    }
    (SITE_DATA_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"exported {len(index)} cases to {SITE_DATA_DIR} (run_id={run_id})")
