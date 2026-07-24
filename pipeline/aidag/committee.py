"""Committee-position (Ja-side) substance, as pipeline data.

Sibling to reservations.py. In the vote the Ja option is only "Utskottets förslag:
Riksdagen avslår motionerna [nr]…" — after doc-ref scrubbing it is hollow, so the
agent (and a reader) never sees WHY the committee holds its line. This layer recovers
the committee's own reasoning (`Utskottets ställningstagande`) for the punkt and turns
it into a short, PARTY-BLIND summary of the Ja position.

ASSOCIATION (the load-bearing problem, validated on HB01SkU15 2026-07-21):
  A betänkande has many `Utskottets ställningstagande` blocks but only some points go
  to a recorded votering, and one punkt can span several blocks. Pure-regex punkt→block
  mapping is unsafe: motion-ids recur across points (a motion's yrkanden are discussed
  in several sections) and the table of contents pollutes heading matches. So we RANK
  candidates deterministically by exact motion-id coverage (top-1 was correct 5/5 on
  HB01SkU15) and hand the top-K to an LLM to ADJUDICATE + summarise, with a confidence
  we can gate on. Determinism narrows; the model judges.

Pipeline (mirrors reservations.py; no API key — Claude Code Workflow):
  <this module>       parse committee blocks, rank top-K candidates per votering, pack
                      request units (rubrik + Ja motions + the Nej demand for context +
                      scrubbed candidate bodies)
  <Workflow haiku>    pick the block that answers THIS point; one party-blind {sv,en}
                      summary of the committee position; return chosen_index+confidence
  committee-ingest    validate (de-leak) + dedupe + append to results JSONL
"""

from __future__ import annotations

import html
import json
import re

import polars as pl

from aidag.config import INTERIM_DIR, PROCESSED_DIR, RAW_DIR, RESULTS_DIR
from aidag.reservations import _read_jsonl, load_reservations, scrub_substance

COMMITTEE_DIR = RESULTS_DIR / "committee"
FETCH_CACHE = RAW_DIR / "betankande"
INTERIM = INTERIM_DIR / "committee"
_MARK = "Utskottets ställningstagande"
TOP_K = 3


def cases_path():
    return COMMITTEE_DIR / "cases.jsonl"


def load_committee() -> dict[str, dict]:
    return {r["votering_id"]: r for r in _read_jsonl(cases_path())}


def _plain(fulltext: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(fulltext))).strip()


def parse_committee_blocks(fulltext: str) -> list[dict]:
    """Every `Utskottets ställningstagande` block, in document order. Each carries the
    exact motion-ids from its own discussion window (the clean span between the previous
    block and this one) and the ställningstagande body up to the next seam."""
    txt = _plain(fulltext)
    marks = [m.start() for m in re.finditer(re.escape(_MARK), txt)]
    blocks: list[dict] = []
    for i, p in enumerate(marks):
        disc_start = marks[i - 1] + len(_MARK) if i > 0 else 0
        window = txt[disc_start:p]
        body_seg = txt[p + len(_MARK):]
        end = re.search(
            r"Reservation\s+\d+\s*\(|Särskilt yttrande|" + re.escape(_MARK) + r"|\bBilaga\b|\Z",
            body_seg,
        )
        body = body_seg[: end.start()].strip() if end else body_seg.strip()
        blocks.append({
            "pos": p,
            "mots": set(re.findall(r"\d{4}/\d{2}:\d+", window)),
            "body": body,
        })
    return blocks


def rank_candidates(want_mots: set[str], blocks: list[dict], k: int = TOP_K) -> list[dict]:
    """Top-k blocks by exact motion-id coverage of the votering's decided motions.
    Coverage (not Jaccard) because a section that also touches other motions is still a
    valid candidate; the LLM disambiguates ties. Falls back to all blocks if no motions."""
    if not want_mots:
        scored = [(0.0, b) for b in blocks]
    else:
        scored = [(len(want_mots & b["mots"]) / len(want_mots), b) for b in blocks]
    scored.sort(key=lambda t: (t[0], -t[1]["pos"]), reverse=True)
    return [b for _, b in scored[:k]]


def _reservations_of(case: dict) -> list[dict]:
    alts = case["alternatives"]
    if isinstance(alts, str):
        alts = json.loads(alts)
    return [a for a in alts if a.get("alt_id") != "utskottet"]


def build_units(dok_id: str, cases: pl.DataFrame) -> list[dict]:
    """One request unit per votering of a betänkande: its context + top-K scrubbed
    candidate committee bodies for the agent to adjudicate + summarise."""
    from aidag.reservations import fetch_fulltext

    blocks = parse_committee_blocks(fetch_fulltext(dok_id))
    res_layer = load_reservations()
    units = []
    for case in cases.filter(pl.col("dok_id") == dok_id).sort("punkt").iter_rows(named=True):
        want = set(re.findall(r"\d{4}/\d{2}:\d+", case.get("forslag_text") or ""))
        cands = rank_candidates(want, blocks)
        # the Nej demand (if any) gives the agent the axis the committee is answering
        nej = None
        for a in _reservations_of(case):
            rec = res_layer.get(f"{case['votering_id']}:{a['alt_id']}")
            if rec:
                nej = rec["subject"]["sv"]
                break
        units.append({
            "votering_id": case["votering_id"],
            "punkt": case["punkt"],
            "topic": case["rubrik"],
            "nej_demand": nej,
            "candidates": [
                {"cand_id": i, "committee_scrubbed": scrub_substance(c["body"])[:2600]}
                for i, c in enumerate(cands)
            ],
        })
    return units


INSTRUCTIONS = (
    "Each unit is one Riksdag decision point with {topic, nej_demand, candidates}. The "
    "candidates are excerpts of the committee's own reasoning; EXACTLY ONE answers THIS "
    "point (use topic and nej_demand to judge which). Pick it (chosen_id) and write ONE "
    "short neutral statement (sv+en) of the committee's substantive position and its main "
    "stated reason, grounded ONLY in the chosen candidate. Rules: PARTY-BLIND (no party, "
    "no politician); present tense; you MAY say the committee sees no reason to change the "
    "rules / points to ongoing work; NO floor-vote outcome, NO document numbers "
    "(SOU/dir./chapter), NO dates. Start the sv with \"Utskottet\". If no candidate clearly "
    "fits, set confidence=\"low\". Copy votering_id. Answer only via the output file."
)


if __name__ == "__main__":
    # step-1 proof: build request units for one betänkande and print the association
    import sys

    dok = sys.argv[1] if len(sys.argv) > 1 else "HB01SkU15"
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    units = build_units(dok, cases)
    for u in units:
        print(f"punkt {u['punkt']:>2} {u['topic'][:34]:34} "
              f"cands={len(u['candidates'])} nej={'yes' if u['nej_demand'] else '—'}")
