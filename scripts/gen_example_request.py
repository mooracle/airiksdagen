#!/usr/bin/env python
"""Regenerate site/src/data/example-request.json — the /om worked example.

The worked example shows the EXACT request one agent receives. It used to be
hand-maintained, and drifted: it still advertised Tidöavtalet and shadow-budget
inputs after p6 dropped bloc documents, so the methodology page described a
corpus the agents no longer read. This script derives every prompt-facing field
from `promptgen`, so the page cannot drift from the code again.

Two parties are shown side by side, chosen to make the p6 split legible: one
whose plan-implied vote MATCHED its floor vote, and one where it diverged at the
`explicit` evidence tier. `a` is the aligned party, `b` the diverging one; the
JSON keys stay `decM`/`docsM` (party a) and `decS`/`docsS` (party b) so the
Astro page needs no restructuring.

  uv run python scripts/gen_example_request.py <votering_id_prefix> <ALIGNED> <DIVERGING>
  uv run python scripts/gen_example_request.py 2C198CDF M S
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from aidag.casemeta import load_casemeta  # noqa: E402
from aidag.compact import compact_meanings  # noqa: E402
from aidag.config import PARTIES, PROCESSED_DIR, RESULTS_DIR, SITE_DATA_DIR  # noqa: E402
from aidag.corpus import documents_for, program_at  # noqa: E402
from aidag.promptgen import (  # noqa: E402
    HALLNING_TO_ROST,
    build_system_blocks,
    evidence_tier,
    render_user_message,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "full-v4"
PROMPT_VERSION = "p6"


def find_case(prefix: str) -> dict:
    df = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    hit = df.filter(pl.col("votering_id").str.to_uppercase().str.starts_with(prefix.upper()))
    if hit.height != 1:
        sys.exit(f"prefix {prefix!r} matched {hit.height} cases — need exactly 1")
    return hit.row(0, named=True)


def load_decision(vid: str, party: str) -> dict:
    path = RESULTS_DIR / "simulations" / RUN_ID / f"{party}.jsonl"
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            if d["votering_id"] == vid:
                return d
    sys.exit(f"no {RUN_ID} decision for {party} on {vid}")


def doc_slug(kind: str, party: str, datum: str) -> str:
    """The /dokument/ slug for a served document, so the page can deep-link it."""
    if kind == "valmanifest":
        return f"valmanifest-2022-{party.lower()}"
    if kind == "partiprogram":
        version = program_at(party, datum)
        return f"partiprogram-{party.lower()}-{version['from'][:4]}" if version else ""
    return ""


def docs_for(party: str, case: dict) -> list[dict]:
    """The documents the p6 agent is actually served, with word counts.

    Straight from `documents_for`, so if the served corpus changes the worked
    example changes with it instead of quietly describing the old one.
    """
    docs = documents_for(
        party, case["datum"], case["rm"], case["votering_id"], PROMPT_VERSION
    )
    return [
        {
            "slug": doc_slug(kind, party, case["datum"]),
            "kind": kind,
            "words": len(text.split()),
        }
        for kind, _tag, text in docs
    ]


def decision_payload(d: dict, actual: str, tr: dict | None = None) -> dict:
    """The decision as the page shows it: stance first, vote derived from it.

    `tr` is this decision's English translation, if it has one. The About page
    already renders `motiveringEn`/`principEn`/`quoteEn` when present — without
    them the English worked example shows Swedish reasoning.
    """
    tr = tr or {}
    tr_cits = tr.get("citations") or []
    return {
        "motiveringEn": tr.get("motivering", ""),
        "hallning": d["hallning"],
        "rost": d["rost"],
        "tier": evidence_tier(d),
        "plan_tacker_utskottets_skal": d.get("plan_tacker_utskottets_skal"),
        "confidence": d["confidence"],
        "coverage": d["coverage"],
        "motivering": d["motivering"],
        "citations": [
            {
                "document": c["document"],
                "quote": c["quote"],
                "princip": c.get("princip", ""),
                # translations are positional — same length and order, enforced
                # by validate_decision_translation at ingest
                "quoteEn": (tr_cits[i].get("quote", "") if i < len(tr_cits) else ""),
                "principEn": (tr_cits[i].get("princip", "") if i < len(tr_cits) else ""),
            }
            for i, c in enumerate(d["citations"])
        ],
        "omvarld": d.get("omvarld") or {"paverkar": False, "faktorer": []},
        "flags": d.get("flags") or [],
        "actual": actual,
        "match": HALLNING_TO_ROST[d["hallning"]] == actual,
    }


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    prefix, party_a, party_b = sys.argv[1], sys.argv[2].upper(), sys.argv[3].upper()
    for p in (party_a, party_b):
        if p not in PARTIES:
            sys.exit(f"unknown party {p!r}")

    case = find_case(prefix)
    vid = case["votering_id"]

    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    actual = {
        r["parti"]: r["position"]
        for r in positions.filter(pl.col("votering_id") == vid).iter_rows(named=True)
    }

    dec_a, dec_b = load_decision(vid, party_a), load_decision(vid, party_b)
    rec = load_casemeta().get(vid) or {}
    subject = rec.get("subject") or {}

    # Prompt text straight from the code that builds it for the real run.
    blocks = build_system_blocks(
        party_a, case["datum"], rm=case["rm"], votering_id=vid, prompt_version=PROMPT_VERSION
    )
    role = blocks[0]["text"]
    user = render_user_message(case, arm="anonymous", prompt_version=PROMPT_VERSION)

    alternatives = case["alternatives"]
    if isinstance(alternatives, str):
        alternatives = json.loads(alternatives)
    meanings = compact_meanings(case["forslag_text"], alternatives)

    from aidag.translate import load_case_translations, load_decision_translations

    tr = load_case_translations().get(vid) or {}
    dtrs = load_decision_translations(RUN_ID)
    cid = lambda p: f"{p}:{vid}:{PROMPT_VERSION}:anonymous"  # noqa: E731
    dtr_a, dtr_b = dtrs.get(cid(party_a)), dtrs.get(cid(party_b))

    out = {
        # provenance, so a reader can tell which run and prompt this shows
        "run_id": RUN_ID,
        "prompt_version": PROMPT_VERSION,
        "votering_id": vid,
        "party_a": party_a,
        "party_b": party_b,
        # drives the page's "not yet translated" notice — read off the data, not
        # hardcoded, so the notice cannot outlive the translation run
        "decisions_translated": bool(dtr_a and dtr_b),
        "roleM_sv": role,
        "user_sv": user,
        "docsM": docs_for(party_a, case),
        "docsS": docs_for(party_b, case),
        "decM": decision_payload(dec_a, actual.get(party_a, ""), dtr_a),
        "decS": decision_payload(dec_b, actual.get(party_b, ""), dtr_b),
        "case": {
            "rubrik_sv": case["rubrik"],
            "rubrik_en": tr.get("rubrik") or case["rubrik"],
            "when_sv": case["datum"],
            "when_en": case["datum"],
            "utskott": case["utskott"],
            "subject_sv": subject.get("sv", ""),
            "subject_en": subject.get("en", "") or subject.get("sv", ""),
            "policy_area": rec.get("policy_area"),
            "ja_sv": meanings["ja_sv"],
            "ja_en": meanings["ja_en"],
            "nej_sv": meanings["nej_sv"],
            "nej_en": meanings["nej_en"],
            "avstar_sv": meanings["avstar_sv"],
            "avstar_en": meanings["avstar_en"],
        },
    }

    dest = SITE_DATA_DIR / "example-request.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"wrote {dest}")
    print(f"  case  {vid} — {case['rubrik']}")
    print(f"  {party_a}: {dec_a['hallning']} -> {dec_a['rost']} vs actual {actual.get(party_a)} "
          f"[{evidence_tier(dec_a)}]")
    print(f"  {party_b}: {dec_b['hallning']} -> {dec_b['rost']} vs actual {actual.get(party_b)} "
          f"[{evidence_tier(dec_b)}]")
    print(f"  docs {party_a}: {[d['kind'] for d in out['docsM']]}")


if __name__ == "__main__":
    main()
