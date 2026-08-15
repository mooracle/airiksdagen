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
    NO_PARTY,
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


def export_blocks_and_anchors(run_id: str | None) -> int:
    """Ship the structured corpus and its citation index to the site.

    Three destinations, and the split between them is the page-weight decision
    (see `anchors.py`):

      src/data/corpus/blocks/<slug>.json    the document itself — roles, pages,
                                            reading order. Read at build time.
      src/data/corpus/anchors/<slug>.json   per-block aggregate counts and the
                                            kept/diverged ratio bar. Also build
                                            time; these render for every cited
                                            block, so they must be cheap.
      public/data/anchors/<slug>.json       the ref lists behind those bars,
                                            fetched per document on demand.

    The 17 documents without blocks (16 budgetmotioner, Tidöavtalet) are simply
    absent here and the page falls back to `formatCorpusDoc()`.

    Returns the number of anchor files written — 0 when the run has no anchors
    yet, which is not an error: `build-anchors` runs after `repair-citations` and
    an export in between should still produce a site. All three no-index cases —
    a missing extraction directory, `run_id=None` (the no-decisions export), and a
    run whose `build-anchors` has not run — return 0 leaving the committed ANCHORS
    untouched. None of them is "a run that cites nothing", and rebuilding the
    directories from their empty payload would delete the committed index rather
    than leave it alone.

    The blocks are not held back with them: they are run-independent, so the two
    latter cases still refresh them. That is what makes those branches a hazard
    rather than a no-op — a refresh moves the positional ids the standing index
    names — so `_check_standing_anchors_survive()` refuses the pairing they cannot
    verify.

    Nothing under `site/src/data/corpus/` is written until every guard has passed.
    Refusing after the copy would leave the tree in exactly the state the guard
    exists to prevent, and — because the guard's own evidence is "the site copy
    differs from the source" — a second `export-site` would then find them equal,
    pass, and ship the mismatched pair.
    """
    from aidag.anchors import anchors_dir, compact_refs, load, summarize
    from aidag.config import BLOCKS_DIR

    corpus_out = SITE_DATA_DIR / "corpus"
    blocks_out = corpus_out / "blocks"
    blocks_out.mkdir(parents=True, exist_ok=True)
    if not BLOCKS_DIR.exists():
        # Same distinction the anchors make below, and `glob` cannot draw it:
        # it answers [] for a missing directory exactly as it does for an empty
        # one, so the sweep would read "nothing is extracted any more" and unlink
        # all 23 committed block files. An empty *directory* is the real "nothing
        # is extracted", and its files are still swept.
        print("  blocks: no extraction directory — committed blocks left alone")
        return 0
    sources = {src.name for src in BLOCKS_DIR.glob("*.json")}
    # Blocks are run-independent and always refreshed, but the committed anchors
    # are not: they index these ids positionally. So note which slugs the refresh
    # would MOVE — a block file that already exists and whose bytes differ, or one
    # `sync_blocks()` is about to unlink because the extraction no longer produces
    # it — and decide on that before writing anything. A slug the site simply did
    # not have yet has moved nothing and is not this case.
    #
    # Removal counts because it is the extreme of the same drift: every id the
    # index names stops existing, and the page falls back to `formatCorpusDoc()`
    # while the committed anchor file for the slug stays behind, orphaned. The
    # indexed path already refuses it (`_check_anchors_match_blocks`, "indexed,
    # but no blocks/<slug>.json"); the no-index branches must not be laxer.
    moved = {
        src.stem
        for src in sorted(BLOCKS_DIR.glob("*.json"))
        if (dst := blocks_out / src.name).exists() and dst.read_bytes() != src.read_bytes()
    } | {dst.stem for dst in sorted(blocks_out.glob("*.json")) if dst.name not in sources}

    def sync_blocks() -> None:
        """Land the refresh. Only ever called once the guards have passed."""
        # same rule as the anchors below: a document that stops being extracted
        # must lose its block file, or the page keeps rendering it from a stale copy
        for stale in blocks_out.glob("*.json"):
            if stale.name not in sources:
                stale.unlink()
        for src in sorted(BLOCKS_DIR.glob("*.json")):
            shutil.copy(src, blocks_out / src.name)

    if run_id is None:
        # `export-site` with no --run-id exports the cases without decisions
        # (cli.py). There is no run to index, so there is nothing to say about
        # the citation anchors — and the rebuild below, driven by an empty
        # payload, would delete the committed index rather than leave it alone.
        _check_standing_anchors_survive(moved, corpus_out)
        sync_blocks()
        return 0
    if not anchors_dir(run_id).exists():
        # The same hazard one step in, and the likelier one: `export-site` before
        # `build-anchors` has ever run for this run_id, or against the synthetic
        # `mock-v1` (README). `load()` answers {} for "no index" exactly as it
        # does for "indexed nothing", so the rebuild below cannot tell them apart
        # and would silently drop 46 committed files. An empty *directory* is the
        # real empty run, and its files are still swept.
        _check_standing_anchors_survive(moved, corpus_out)
        sync_blocks()
        print(f"  anchors: {run_id} has no index — committed anchors left alone")
        return 0

    payloads = load(run_id)
    # The blocks about to ship and the index loaded here are two artifacts of two
    # passes, and nothing so far has checked that they describe the SAME
    # extraction. `build-anchors` asserts the pairing (`anchors.block_index`),
    # but this path does not go through it: re-extract and then export before
    # re-indexing — a sequence the no-index branches above deliberately allow —
    # and the site ships new blocks against old anchors. Block ids are positional
    # (`docx.assign_roles` numbers them `b{i:04d}`), so one added or removed block
    # shifts every id after it and attributes real votes to lines nobody cited,
    # with plausible numbers and nothing raised. Refuse instead of shipping it.
    #
    # Checked against BLOCKS_DIR rather than `blocks_out`: these are the bytes
    # `sync_blocks()` is about to write, so the two are equivalent — except that
    # reading the source lets the refusal happen before the site tree is touched.
    _check_anchors_match_blocks(payloads, BLOCKS_DIR)
    sync_blocks()
    summaries = {slug: summarize(p) for slug, p in payloads.items()}

    anchors_out = corpus_out / "anchors"
    public_out = SITE_DATA_DIR.parents[1] / "public" / "data" / "anchors"
    # rebuilt, not merged: a slug that stops being cited must lose its file
    # rather than keep serving the previous run's refs
    for d in (anchors_out, public_out):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for slug, payload in payloads.items():
        (anchors_out / f"{slug}.json").write_text(
            json.dumps(summaries[slug], ensure_ascii=False), encoding="utf-8"
        )
        (public_out / f"{slug}.json").write_text(
            json.dumps(compact_refs(payload), ensure_ascii=False), encoding="utf-8"
        )
    return len(payloads)


class StaleAnchors(Exception):
    """The citation index was built against a different extraction than the blocks."""


def _check_standing_anchors_survive(moved: set[str], corpus_out) -> None:
    """A block refresh must not orphan the committed index the run cannot re-check.

    The two no-index branches return before `_check_anchors_match_blocks()` runs,
    and the blocks refresh on every export — so `extract-corpus --force` followed
    by `export-site` with no `--run-id` (or against a run with no index, such as
    README's `mock-v1`) would ship a NEW extraction against the PREVIOUS run's
    anchors, which is the failure the pairing guard exists for. Those
    branches have no payload to round-trip quotes against — the site-side files
    hold offsets, not quote text (`anchors.summarize`/`compact_refs`) — but they
    do not need one: a block file that was rewritten — or dropped — under a
    standing anchor file for the same slug has moved ids the index still names,
    and that is enough to refuse on.
    """
    standing = {p.stem for p in (corpus_out / "anchors").glob("*.json")}
    orphaned = sorted(moved & standing)
    if orphaned:
        raise StaleAnchors(
            "the extracted blocks were rewritten or removed under a committed "
            f"citation index that this export cannot re-check: {', '.join(orphaned[:3])}"
            f"{', …' if len(orphaned) > 3 else ''}\n"
            "Block ids are positional, so shipping these together would attribute "
            "real votes to the wrong lines. Re-run `uv run aidag build-anchors "
            "--run-id <run>` and export with that --run-id."
        )


def _check_anchors_match_blocks(payloads: dict[str, dict], blocks_dir) -> None:
    """Every anchor must still name, and still fit, the block it was built against.

    Not an id-existence check: an inserted block shifts every id after it while
    leaving them all in range, so "the id exists" would pass the very case this
    is for. The quote is in the index, so the span is re-read from the block that
    is about to ship and compared against it — the same round-trip
    `tests/test_anchors.py::TestDrawnSpans` pins, run on the artifact rather than
    on the repo.

    Anchors whose quote runs past their block into the next one (143 of full-v4's
    16,707) cannot be round-tripped whole — re-joining the document here to prove
    them would be `block_index()` a second time — so they are checked against the
    part that IS in this block: the span must be its non-empty tail and must be a
    prefix of the quote. Skipping them outright instead would exempt every anchor
    that no longer fits its block, which is the drift itself.
    """
    from aidag.anchors import drawn

    stale: list[str] = []
    for slug, payload in sorted(payloads.items()):
        path = blocks_dir / f"{slug}.json"
        if not path.exists():
            stale.append(f"  {slug}: indexed, but no blocks/{slug}.json to render it against")
            continue
        text = {
            b["id"]: drawn(b["text"])
            for b in json.loads(path.read_text(encoding="utf-8"))["blocks"]
        }
        missing, moved = [], []
        for a in payload["anchors"]:
            block = text.get(a["block_id"])
            if block is None:
                missing.append(a["block_id"])
                continue
            quote = drawn(a["quote"])
            span = block[a["offset"] : a["offset"] + a["length"]]
            if len(span) == a["length"]:
                ok = span == quote
            else:
                # A short span is NOT self-evidently the cross-block case. An
                # anchor whose block has shrunk under it reads short too, and one
                # whose offset now falls off the end reads empty — so excusing
                # every short span waves through exactly the drift this guards:
                # re-point 200 anchors at a one-character block and all 200 pass.
                # The cases separate cleanly. A quote that continues into the next
                # block starts with the WHOLE tail of this one, so the span must be
                # non-empty and must be a prefix of the quote. All 143 of full-v4's
                # cross-block anchors satisfy that; nothing else does.
                ok = bool(span) and quote.startswith(span)
            if not ok:
                moved.append(f"{a['block_id']} ({span[:40]!r} for {quote[:40]!r})")
        for label, rows in (("not in", missing), ("moved in", moved)):
            if rows:
                stale.append(
                    f"  {slug}: {len(rows)} anchor(s) {label} blocks/{slug}.json "
                    f"({', '.join(sorted(set(rows))[:3])}{', …' if len(set(rows)) > 3 else ''})"
                )
    if stale:
        listed = "\n".join(stale)
        raise StaleAnchors(
            "the citation index and the extracted blocks describe different "
            f"extractions:\n{listed}\n"
            "Block ids are positional, so shipping these together would attribute "
            "real votes to the wrong lines. Re-run `uv run aidag build-anchors "
            "--run-id <run>` after `extract-corpus`, before exporting."
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
    from aidag.translate import load_decision_translations, withhold_unverified

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
            tr = withhold_unverified(translations.get(cid), d["citations"])
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
    # Independents (`parti` == '-') are kept. They are seated members whose
    # votes decide divisions, and filtering them here is what made every
    # downstream total an eight-party subtotal — see analytics._chamber_totals.
    # `positions` above stays party-keyed and therefore stays eight parties;
    # only the per-seat array and the chamber totals gain the extra members.
    votes = (
        pl.read_parquet(PROCESSED_DIR / "votes.parquet")
        .filter(pl.col("avser") == "sakfrågan", pl.col("votering") == "huvud")
        .with_columns(pl.col("parti").str.to_uppercase())
        .filter(pl.col("parti").is_in([*PARTIES, NO_PARTY]))
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
        # Votes cast by members outside every party group. `actual` is keyed by
        # party and structurally cannot hold them, so they are counted here and
        # passed in separately; they are added to both sides of the
        # counterfactual and never moved.
        independents = case_votes.filter(pl.col("parti") == NO_PARTY)
        others = {
            "n_ja": int((independents["rost"] == "Ja").sum()),
            "n_nej": int((independents["rost"] == "Nej").sum()),
        }
        flip = aivotes.flip(actual, case_decisions, others)
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
    # Blocks and anchors FIRST: their guards refuse a mismatched extraction, and
    # `export_corpus()` writes the derived .txt into the same directory. Run the
    # other way round and a `StaleAnchors` refusal leaves the site tree holding
    # the new text beside the previous extraction's blocks and anchors — a state
    # `git status` shows as a plausible diff, and README's publish recipe adds
    # wholesale.
    n_anchors = export_blocks_and_anchors(run_id)
    export_corpus()
    print(f"exported blocks for {len(list(SITE_DATA_DIR.glob('corpus/blocks/*.json')))} documents, "
          f"anchors for {n_anchors}")

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
