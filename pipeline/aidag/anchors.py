"""Reverse index: which votes leaned on which line of which document.

Citations run one way. A decision names a document class and a verbatim quote,
and the site re-finds that quote in the browser with a `#:~:text=` fragment. The
document page cannot answer the other question — *this* sentence, who cited it,
and did they then vote with it or against it — because nothing in the record
points from a line back to the votes.

This module builds that direction. For every citation in a run it locates the
quote in the re-extracted corpus and records a ref against the block it landed
in, so `data/results/anchors/<run_id>/<slug>.json` is the document read as a
citation index.

**Quote is the key; offsets are derived here.** Storing a character offset in the
record would make the link a function of the extraction, and re-extraction is
half of what this plan does — every reformat would silently move every anchor.
The quote survives a reformat, so the locator runs at build time and the offsets
are outputs, not inputs. 99.7% of quotes are unique within their document, and a
repeat is the same sentence anyway; the first occurrence is taken.

**The locator has to agree with `verify simulate` or the gate means nothing.**
Both compare whitespace-collapsed text against `corpus.normalize()` output, and
this module gets there by reusing `migrate_quotes.index_for()` — the same served
string, built the same way. What it adds is the block identity: the served
document is one line per surviving block (`extract_corpus.text_from_blocks`), so
the lines ARE the blocks. The pairing is asserted per block rather than assumed,
because a drifting block file would otherwise attribute every anchor in the
document to the wrong line.

**A miss fails the build.** Silent link-rot is the exact failure quote-as-key
exists to prevent: a quote that no longer resolves would simply vanish from the
index, and the document page would show a line as uncited. Three outcomes are
accepted instead — a blank quote (`repair-citations` withdrew it), a decision
flagged `citat_ej_migrerat` (Task 5 could not place it and said so), and nothing
else. Anything else raises with the quotes listed.

Below p6 nothing is indexed: those runs read `data/corpus/frozen/`, and locating
their quotes against the re-extracted blocks would anchor them to a document
they were never shown.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache

from aidag.config import RESULTS_DIR
from aidag.migrate_quotes import MIGRATED_FROM, index_for, resolve_slug
from aidag.simulate import _normalize_ws

# What the party's own plan implied, against what the party actually did.
#
# `rost` is DERIVED from `hallning` at ingest on a p6 run (see
# promptgen.HALLNING_TO_ROST) — it is the vote the plan implies, not a prediction
# of the party's behaviour. So this is the plan-vs-behaviour GAP that gap.py
# reports, and emphatically not "did the model guess the vote right".
#
# `gap.py:121` scores only ("Ja", "Nej") and `analytics.py` excludes absence
# throughout, for the same reason: an abstention is a floor tactic the plan was
# never asked to express, and an absent party did not take a side at all. Both
# are carried here as their own verdicts rather than folded into `diverged`, so
# the site can show them without asserting a divergence that was never measured.
KEPT, DIVERGED, AVSTAR, FRANVARANDE = "kept", "diverged", "avstar", "franvarande"
VERDICTS = (KEPT, DIVERGED, AVSTAR, FRANVARANDE)

# Only the anonymous arm reaches the site (`export_site.load_decisions_by_case`),
# and an index that mixed arms would double-count every quote they share.
ARM = "anonymous"

REF_FIELDS = ("votering_id", "parti", "verdict", "tier", "utskott", "datum", "svag")


def anchors_dir(run_id: str, results_dir=None):
    return (results_dir or RESULTS_DIR) / "anchors" / run_id


def verdict_of(rost: str | None, position: str | None) -> str:
    """The gap verdict for one decision.

    A party with no row in `party_positions` is absent, not missing data:
    `build_cases` writes a row per party that holds seats in the division and
    labels a party whose members all stayed away `Frånvarande`, so "no row" and
    "Frånvarande" are the same fact arriving two ways.
    """
    if position == "Avstår":
        return AVSTAR
    if position in (None, "Frånvarande"):
        return FRANVARANDE
    return KEPT if position == rost else DIVERGED


@dataclass(slots=True)
class BlockIndex:
    """One served document, addressable by block.

    `served` and `starts`/`ends` come from `migrate_quotes.index_for`, so the
    text is byte-for-byte what `verify simulate` compares a citation against.
    `ids` names the block each span belongs to.
    """

    slug: str
    served: str
    ids: list[str]
    starts: list[int]
    ends: list[int]

    def block_at(self, pos: int) -> int:
        """Index of the block holding character `pos` of the served text."""
        i = bisect_right(self.starts, pos) - 1
        if i < 0 or pos >= self.ends[i]:
            # the single space `index_for` joins two blocks with. A collapsed
            # quote never starts on one, but a caller could ask.
            return -1
        return i

    def locate(self, quote: str) -> tuple[str, int] | None:
        """(block_id, offset within the block) for a quote, or None.

        Exact substring only. A fuzzy locator here would re-do the judgement
        `migrate-quotes` already made against the research record, in a build
        step that cannot write its result back — so a quote that does not
        resolve is a fact about the record, to be reported and not repaired.
        """
        q = _normalize_ws(quote)
        if not q:
            return None
        pos = self.served.find(q)
        if pos < 0:
            return None
        i = self.block_at(pos)
        if i < 0:
            return None
        return self.ids[i], pos - self.starts[i]


@lru_cache(maxsize=64)
def block_index(slug: str) -> BlockIndex:
    """Pair `blocks/<slug>.json` with the served text, checking they agree.

    `corpus.normalize()` drops page numbers, rules and undecodable display-font
    headings, so the blocks that survive into the served text are the ones
    `extract_corpus.normalize_drops` does not claim. Pairing them positionally is
    only correct while that filter and `normalize()` agree about every block, and
    a disagreement would not be visible in the output — it would shift every id
    after it and attribute real citations to the wrong lines. So each pairing is
    checked, and a mismatch raises.
    """
    from aidag.extract_corpus import blocks_path, normalize_drops

    idx = index_for(slug)
    path = blocks_path(slug)
    if not path.exists():
        raise FileNotFoundError(
            f"{slug}: no blocks at {path} — run `uv run aidag extract-corpus`"
        )
    kept = [b for b in json.loads(path.read_text())["blocks"] if not normalize_drops(b["text"])]
    if len(kept) != len(idx.spans):
        raise ValueError(
            f"{slug}: {len(kept)} blocks survive normalize() but the served text has "
            f"{len(idx.spans)} lines — blocks/ and the .txt have drifted apart; "
            "re-run `uv run aidag extract-corpus --force`"
        )
    for b, (lo, hi) in zip(kept, idx.spans):
        if idx.served[lo:hi] != _normalize_ws(b["text"]):
            raise ValueError(
                f"{slug}: block {b['id']} does not match the served line at {lo} "
                f"({b['text'][:60]!r} vs {idx.served[lo:hi][:60]!r})"
            )
    return BlockIndex(
        slug=slug,
        served=idx.served,
        ids=[b["id"] for b in kept],
        starts=[lo for lo, _ in idx.spans],
        ends=[hi for _, hi in idx.spans],
    )


@dataclass(slots=True)
class Anchor:
    quote: str
    block_id: str
    offset: int
    refs: list[dict]

    def to_dict(self) -> dict:
        return {
            "quote": self.quote,
            "block_id": self.block_id,
            "offset": self.offset,
            "length": len(_normalize_ws(self.quote)),
            "refs": self.refs,
        }


class Unlocated(Exception):
    """One or more citations no longer resolve, and none of them was excused."""


def ref_for(d: dict, c: dict, case: dict, position: str | None) -> dict:
    """The seven fields a document page shows about one citing vote."""
    from aidag.promptgen import evidence_tier

    return {
        "votering_id": d["votering_id"],
        "parti": d["parti"],
        "verdict": verdict_of(d.get("rost"), position),
        # explicit | extrapolated | off_axis; None on p4/p5, which this build
        # never reaches. Only `explicit` can carry a broken-commitment claim.
        "tier": evidence_tier(d) if d.get("hallning") else None,
        "utskott": case.get("utskott"),
        "datum": case.get("datum"),
        # a supporting-but-generic quote (blocklist.WEAK_LIST); kept in the index
        # so the page can show it as weaker evidence rather than drop it
        "svag": bool(c.get("svag")),
    }


def _sort_key(anchor: Anchor, order: dict[str, int]) -> tuple[int, int]:
    return (order.get(anchor.block_id, 1 << 30), anchor.offset)


def collect(decisions, cases: dict, positions: dict) -> tuple[dict[str, list[Anchor]], Counter, list]:
    """Walk a run's decisions and group every citation under its anchor.

    Returns (anchors by slug, counters, unlocated). `unlocated` holds only the
    misses the record does not excuse — the caller raises on those.
    """
    counts: Counter = Counter()
    unlocated: list[dict] = []
    by_slug: dict[str, dict[tuple[str, str], Anchor]] = defaultdict(dict)
    for d in decisions:
        if d.get("arm", ARM) != ARM:
            counts["other_arm"] += 1
            continue
        if d["prompt_version"] < MIGRATED_FROM:
            # pre-p6 runs read data/corpus/frozen/; these blocks are not their text
            counts["pre_p6"] += 1
            continue
        counts["decisions"] += 1
        case = cases.get(d["votering_id"], {})
        position = positions.get((d["votering_id"], d["parti"]))
        excused = "citat_ej_migrerat" in (d.get("flags") or [])
        for c in d.get("citations", []):
            quote = c.get("quote") or ""
            if not quote:
                # repair-citations withdrew it; the agent's words are in
                # `quote_ej_verifierad` and are not a claim about the corpus
                counts["blank"] += 1
                continue
            slug = resolve_slug(c["document"], d["parti"], case.get("datum", ""))
            if slug is None:
                # budgetmotioner and Tidöavtalet keep the flat extraction and have
                # no blocks to anchor into. p6 shows neither, so this stays at 0.
                counts["out_of_scope"] += 1
                continue
            hit = block_index(slug).locate(quote)
            if hit is None:
                counts["unlocated"] += 1
                row = {"document": slug, "quote": quote, "cid": d["votering_id"], "parti": d["parti"]}
                if excused:
                    counts["unlocated_excused"] += 1
                else:
                    unlocated.append(row)
                continue
            block_id, offset = hit
            key = (block_id, _normalize_ws(quote))
            anchor = by_slug[slug].get(key)
            if anchor is None:
                anchor = Anchor(quote=_normalize_ws(quote), block_id=block_id, offset=offset, refs=[])
                by_slug[slug][key] = anchor
                counts["anchors"] += 1
            ref = ref_for(d, c, case, position)
            if ref not in anchor.refs:
                # one decision citing the same line twice is one vote, not two
                anchor.refs.append(ref)
                counts["refs"] += 1
                counts[f"verdict:{ref['verdict']}"] += 1
            else:
                counts["duplicate_ref"] += 1
            counts["located"] += 1
    out: dict[str, list[Anchor]] = {}
    for slug, anchors in by_slug.items():
        order = {bid: i for i, bid in enumerate(block_index(slug).ids)}
        out[slug] = sorted(anchors.values(), key=lambda a: _sort_key(a, order))
        for a in out[slug]:
            a.refs.sort(key=lambda r: (r["datum"] or "", r["votering_id"], r["parti"]))
    return out, counts, unlocated


def _load_run(run_id: str, results_dir=None):
    root = (results_dir or RESULTS_DIR) / "simulations" / run_id
    for path in sorted(root.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)


def _case_tables():
    import polars as pl

    from aidag.config import PROCESSED_DIR

    cases = pl.read_parquet(
        PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum", "utskott"]
    )
    positions = pl.read_parquet(
        PROCESSED_DIR / "party_positions.parquet", columns=["votering_id", "parti", "position"]
    )
    return (
        {r["votering_id"]: r for r in cases.iter_rows(named=True)},
        {(r["votering_id"], r["parti"]): r["position"] for r in positions.iter_rows(named=True)},
    )


def write(run_id: str, by_slug: dict[str, list[Anchor]], results_dir=None) -> list[dict]:
    out_dir = anchors_dir(run_id, results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for slug in sorted(by_slug):
        anchors = by_slug[slug]
        n_refs = sum(len(a.refs) for a in anchors)
        (out_dir / f"{slug}.json").write_text(
            json.dumps(
                {
                    "slug": slug,
                    "run_id": run_id,
                    "n_anchors": len(anchors),
                    "n_refs": n_refs,
                    "anchors": [a.to_dict() for a in anchors],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report.append({"slug": slug, "anchors": len(anchors), "refs": n_refs})
    return report


def build(run_id: str, results_dir=None) -> dict:
    """Locate every citation in a run and write the per-document anchor files."""
    cases, positions = _case_tables()
    by_slug, counts, unlocated = collect(_load_run(run_id, results_dir), cases, positions)
    if unlocated:
        listed = "\n".join(
            f"  {u['document']}: {u['quote'][:100]}" for u in unlocated[:20]
        )
        more = f"\n  ... and {len(unlocated) - 20} more" if len(unlocated) > 20 else ""
        raise Unlocated(
            f"{len(unlocated)} citation(s) no longer resolve to a block and are neither "
            f"blank nor flagged `citat_ej_migrerat`:\n{listed}{more}\n"
            "Anchors are keyed on the quote, so an unlocated citation is a link that "
            "would silently disappear. Re-run migrate-quotes, or list the quote in "
            "data/corpus/known-unrecovered.json if the extraction genuinely cannot "
            "produce it."
        )
    report = write(run_id, by_slug, results_dir)
    for row in report:
        print(f"  {row['slug']}: {row['anchors']} anchors, {row['refs']} refs")
    print(
        f"{run_id}: {counts['decisions']} decisions, {counts['located']} citations located "
        f"into {counts['anchors']} anchors ({counts['refs']} refs), {counts['blank']} blank, "
        f"{counts['unlocated_excused']} unlocated but flagged, {counts['pre_p6']} pre-p6 skipped"
    )
    print(
        "  verdicts: "
        + " | ".join(f"{v} {counts[f'verdict:{v}']}" for v in VERDICTS)
    )
    return {"counts": counts, "report": report}


def run(run_id: str) -> dict:
    return build(run_id)


# --- site shapes ---------------------------------------------------------
#
# Page weight, decided up front rather than discovered on a 9,007-ref document.
# `valmanifest-2022-sd` carries 9,007 refs and the corpus as a whole 77,599; at
# seven fields each, inlining them would put multiple megabytes of static HTML on
# a single document page for a panel most readers never open.
#
# So the split is by what the page shows before a click. Aggregate counts and the
# kept/diverged ratio bar render for every cited block and are therefore INLINE,
# built into `site/src/data/corpus/anchors/` at build time — no quote text, since
# the block already holds it and offset+length recover the span. The ref lists
# behind them are fetched per document from `site/public/data/anchors/`, in a
# row encoding keyed by REF_FIELDS: the field names repeat 77,599 times
# otherwise, and they are the larger half of the payload.


def load(run_id: str, results_dir=None) -> dict[str, dict]:
    """Every per-document anchor file a run wrote, keyed by slug."""
    out = {}
    d = anchors_dir(run_id, results_dir)
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        out[path.stem] = json.loads(path.read_text())
    return out


def _tally(refs: list[dict]) -> dict:
    counts = {v: 0 for v in VERDICTS}
    for r in refs:
        counts[r["verdict"]] += 1
    return {"refs": len(refs), **counts}


def _decision_tally(refs: list[dict]) -> dict:
    """The same tally over DISTINCT deciding votes rather than citations.

    A decision may quote two spans of one block — 1,679 of full-v4's 4,715 cited
    blocks carry at least one, and the corpus reads 77,599 citations against
    69,418 distinct (decision, block) pairs. Counting those twice is right for a
    citation total and wrong for the sentence the page actually makes: "N votes
    leaned on this line, X of them with the plan". Both are kept, named for what
    they count.
    """
    seen = {(r["votering_id"], r["parti"]): r["verdict"] for r in refs}
    counts = {v: 0 for v in VERDICTS}
    for verdict in seen.values():
        counts[verdict] += 1
    return {"n": len(seen), **counts}


def summarize(payload: dict) -> dict:
    """The inline aggregate: per block, and per anchor within it.

    `refs`/`kept`/`diverged`/... count citations; `decisions` counts the votes
    behind them. `tiers` and `parties` are properties OF a decision, not of a
    citation, so they are tallied per distinct decision too — a histogram
    weighted by how often a vote happened to quote the same line twice is not a
    histogram of anything. `svag` stays a citation count: weakness is a property
    of the quote (`blocklist.WEAK_LIST`), and one vote can cite a line both ways.
    """
    blocks: dict[str, dict] = {}
    per_block_refs: dict[str, list[dict]] = defaultdict(list)
    for a in payload["anchors"]:
        b = blocks.setdefault(
            a["block_id"],
            {**_tally([]), "svag": 0, "decisions": {}, "tiers": Counter(),
             "parties": Counter(), "anchors": []},
        )
        tally = _tally(a["refs"])
        for k, n in tally.items():
            b[k] += n
        per_block_refs[a["block_id"]].extend(a["refs"])
        for r in a["refs"]:
            b["svag"] += bool(r["svag"])
        b["anchors"].append({"offset": a["offset"], "length": a["length"], **tally})
    for block_id, b in blocks.items():
        refs = per_block_refs[block_id]
        b["decisions"] = _decision_tally(refs)
        by_decision = {(r["votering_id"], r["parti"]): r for r in refs}
        for r in by_decision.values():
            b["tiers"][r["tier"]] += 1
            b["parties"][r["parti"]] += 1
        b["tiers"] = dict(sorted(b["tiers"].items(), key=lambda kv: (kv[0] is None, kv[0])))
        b["parties"] = dict(sorted(b["parties"].items()))
    all_refs = [r for a in payload["anchors"] for r in a["refs"]]
    return {
        "slug": payload["slug"],
        "run_id": payload["run_id"],
        "totals": {
            "anchors": payload["n_anchors"],
            **_tally(all_refs),
            # distinct across the whole document: a vote citing four of its lines
            # is one vote that read this document, not four
            "decisions": _decision_tally(all_refs),
        },
        "blocks": blocks,
    }


def compact_refs(payload: dict) -> dict:
    """The fetched-on-demand half: the same refs as rows keyed by REF_FIELDS."""
    return {
        "slug": payload["slug"],
        "run_id": payload["run_id"],
        "fields": list(REF_FIELDS),
        "anchors": [
            {
                "block_id": a["block_id"],
                "offset": a["offset"],
                "length": a["length"],
                "refs": [[r[f] for f in REF_FIELDS] for r in a["refs"]],
            }
            for a in payload["anchors"]
        ],
    }


# --- reporting: what kind of line does a citation land on? -------------------
#
# The finding this plan set out to measure. A quote can be verbatim from a party
# programme and still not be a commitment — "EN STRAM MIGRATION" is a topic
# label, and a vote resting on it has cited a heading, not a promise. The site
# marks these per block (`CiteNote`); this reports them corpus-wide, by party and
# by document, which is what makes the size of the effect a fact rather than an
# impression.
#
# The rule is duplicated from `site/src/lib/anchors.ts:isNavigational` rather
# than shared, because the two run in different languages over different inputs
# (rendered groups there, the committed anchor files here). Duplication is a
# drift risk, so the report is checked against the site's own count in
# `tests/test_anchors.py::TestNavigationParity` instead of trusted.

NAV_ROLES = frozenset({"h1", "h2", "h3", "label", "toc"})
NAV_MAX_WORDS = 8
_BULLET_GLYPH = re.compile(r"^\s*[•▪◦·]")
_STATES_SOMETHING = re.compile(r"[.!?][\"»”']?$")


def is_navigational(role: str, text: str) -> bool:
    """True for a cited line that names a topic rather than stating anything.

    Role alone is NOT the test, and taking it for one would make a false claim
    1,163 times. `label` is "a minor bold cluster at or below body size", which
    in `partiprogram-kd-2015` is a back-cover topic list but in
    `valmanifest-2022-s` is the bold bullet list of the party's actual pledges
    ("• Kraftigt öka antalet poliser…") and in `valmanifest-2022-l` its 60
    numbered ones. So the text has to read as a label too: no bullet glyph, no
    sentence-ending punctuation, and short.

    `toc` is exempt from all three — a contents entry is navigation whatever it
    says, that being what a contents list is.
    """
    if role == "toc":
        return True
    if role not in NAV_ROLES:
        return False
    t = text.strip()
    if _BULLET_GLYPH.match(t) or _STATES_SOMETHING.search(t):
        return False
    return len(t.split()) <= NAV_MAX_WORDS


@lru_cache(maxsize=64)
def block_roles(slug: str) -> dict[str, dict]:
    """`blocks/<slug>.json` as {block_id: {role, text, page}}."""
    from aidag.extract_corpus import blocks_path

    path = blocks_path(slug)
    if not path.exists():
        raise FileNotFoundError(
            f"{slug}: no blocks at {path} — run `uv run aidag extract-corpus`"
        )
    return {
        b["id"]: {"role": b["role"], "text": b["text"], "page": b["page"]}
        for b in json.loads(path.read_text())["blocks"]
    }


def _refs_by_block(payload: dict) -> dict[str, list[dict]]:
    """One document's refs regrouped under the block they cite.

    Per block, not per anchor: two spans of one line are two anchors and one
    cited line, and the question here is about the line.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    for a in payload["anchors"]:
        out[a["block_id"]].extend(a["refs"])
    return out


def _votes(refs) -> set[tuple[str, str]]:
    """The distinct deciding votes behind a pile of citations."""
    return {(r["votering_id"], r["parti"]) for r in refs}


def cited_blocks(run_id: str, results_dir=None) -> list[dict]:
    """Every cited block in a run, with its role, its text and its tallies.

    Raises if a cited block is missing from `blocks/*.json`: the anchor files
    were built from those blocks, so a gap means the two have drifted and every
    role in this report would be a guess.
    """
    rows = []
    for slug, payload in sorted(load(run_id, results_dir).items()):
        meta = block_roles(slug)
        for block_id, refs in _refs_by_block(payload).items():
            b = meta.get(block_id)
            if b is None:
                raise ValueError(
                    f"{slug}: anchors cite block {block_id}, which is not in "
                    "blocks/ — re-run `uv run aidag build-anchors`"
                )
            rows.append(
                {
                    "slug": slug,
                    "block_id": block_id,
                    "role": b["role"],
                    "text": b["text"],
                    "page": b["page"],
                    "citations": len(refs),
                    "votes": _votes(refs),
                    "navigational": is_navigational(b["role"], b["text"]),
                }
            )
    rows.sort(key=lambda r: (r["slug"], r["block_id"]))
    return rows


def _agg(rows: list[dict]) -> dict:
    """Blocks, citations and votes over a set of cited blocks.

    `decisions` counts each vote once however many of these blocks it cited;
    `block_decisions` sums the per-block counts. They differ exactly when a vote
    cited two blocks in scope, and both are reported because both get used: the
    per-block number is what a document page adds up, the distinct number is
    what a sentence like "N votes rested on a heading" has to mean.
    """
    votes: set[tuple[str, str]] = set()
    block_decisions = 0
    citations = 0
    for r in rows:
        votes |= r["votes"]
        block_decisions += len(r["votes"])
        citations += r["citations"]
    return {
        "blocks": len(rows),
        "citations": citations,
        "decisions": len(votes),
        "block_decisions": block_decisions,
    }


def _split(rows: list[dict], key) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[key(r)].append(r)
    agg = {k: _agg(v) for k, v in grouped.items()}
    return dict(sorted(agg.items(), key=lambda kv: (-kv[1]["decisions"], kv[0])))


def _by_party(rows: list[dict]) -> dict[str, dict]:
    """Citing party, taken from the refs rather than from the document's slug.

    In practice a party only cites its own documents, so the two agree — but
    that is a fact about the corpus, and reading it off the refs keeps it one.
    """
    votes: dict[str, set] = defaultdict(set)
    for r in rows:
        for vid, parti in r["votes"]:
            votes[parti].add(vid)
    return {
        parti: {"decisions": len(v)}
        for parti, v in sorted(votes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    }


def _listed(r: dict) -> dict:
    return {
        "slug": r["slug"],
        "block_id": r["block_id"],
        "role": r["role"],
        "page": r["page"],
        "text": r["text"],
        "citations": r["citations"],
        "decisions": len(r["votes"]),
        "parties": sorted({p for _, p in r["votes"]}),
    }


def _scope(rows: list[dict], listed: int | None = None) -> dict:
    """One slice of the cited blocks, broken down the way the finding needs it."""
    ranked = sorted(rows, key=lambda r: (-len(r["votes"]), r["slug"], r["block_id"]))
    return {
        **_agg(rows),
        "by_party": _by_party(rows),
        "by_document": _split(rows, lambda r: r["slug"]),
        "by_role": _split(rows, lambda r: r["role"]),
        "blocks_listed": [_listed(r) for r in ranked[:listed]],
    }


def navigation_report(run_id: str, results_dir=None, rows: list[dict] | None = None) -> dict:
    """Corpus-wide: which citations landed on navigation, by party and document.

    Three scopes, because they are three different numbers and only naming each
    one keeps them apart:

    - `label_toc` — this plan's literal question, citations landing on a `label`
      or `toc` block. **1,165 votes**, and it is the wrong answer: `label` is a
      typographic class ("a minor bold cluster at or below body size"), and in
      two manifestos it is the party's own pledge list.
    - `role_only` — the same test over every navigational role, headings
      included, because KD's back-cover topic labels are roled `h3`.
    - `navigational` — role AND the text reading as a label, which is what the
      site marks. **16 blocks / 178 votes.**

    The gap between the first and the last IS the finding. A report printing
    only the final number would leave the next reader to re-derive why it is not
    the obvious one, and re-deriving it is how the false claim gets made.
    """
    rows = rows or cited_blocks(run_id, results_dir)
    return {
        "run_id": run_id,
        "totals": _agg(rows),
        "by_role": _split(rows, lambda r: r["role"]),
        "navigational": _scope([r for r in rows if r["navigational"]], listed=None),
        "label_toc": _scope([r for r in rows if r["role"] in ("label", "toc")], listed=10),
        "role_only": _scope([r for r in rows if r["role"] in NAV_ROLES], listed=10),
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "—"


# `blocklist.WEAK_LIST` was the route this finding was originally expected to
# take, and it was cut on review: an entry names a document CLASS, not a slug,
# and matching is bidirectional containment (`blocklist.py:139`), so a short
# topic label registered against `valmanifest` is tested against all eight
# parties' manifestos in both directions. That is an argument, and an argument
# is not a measurement — so the report scores every flagged phrase against the
# bar the module states ("long and distinctive enough that either-direction
# containment cannot catch a genuine policy quote") using the committed
# citations as the corpus of things that must not be caught.


def _class_of(slug: str) -> str:
    """The document class a citation names — `valmanifest` or `partiprogram`."""
    return slug.split("-", 1)[0]


@lru_cache(maxsize=1)
def _served_by_class() -> dict[str, list[tuple[str, str]]]:
    """(slug, matching-form served text) per document class, for every document.

    The class is what a WEAK_LIST entry names, so this is the set an entry is
    tested against — all eight parties, not the one whose page the phrase sits
    on.
    """
    from aidag.blocklist import _key
    from aidag.extract_corpus import cited_slugs

    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for slug in cited_slugs():
        try:
            served = index_for(slug).served
        except FileNotFoundError:
            continue
        out[_class_of(slug)].append((slug, _key(served)))
    return dict(out)


def weak_list_candidates(run_id: str, results_dir=None, rows: list[dict] | None = None) -> list[dict]:
    """Each flagged navigation phrase, scored against the WEAK_LIST bar.

    Two tests, and they answer different questions.

    `catches`/`false_positives` are measured against the committed citations:
    every quote the entry would mark across every party's document of the same
    class, and how many of those are not this line. That is evidence, over the
    16,723 quotes that exist.

    `other_documents` is the bar `blocklist.py` actually states — "long and
    distinctive enough that either-direction containment cannot catch a genuine
    policy quote" — tested against the DOCUMENTS rather than the quotes: if the
    phrase occurs verbatim in another party's document of the same class, then
    a future citation of that passage would be marked `svag`, and no committed
    quote has to exist yet for that to be true. A phrase clean on the first test
    and dirty on this one is a trap, not a candidate.
    """
    from aidag.blocklist import _key

    rows = [r for r in (rows or cited_blocks(run_id, results_dir)) if r["navigational"]]
    # distinct quotes per document class, with the votes that used each
    quotes: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for d in _load_run(run_id, results_dir):
        if d.get("arm", ARM) != ARM or d["prompt_version"] < MIGRATED_FROM:
            continue
        for c in d.get("citations", []):
            if q := c.get("quote") or "":
                quotes[c["document"]][_key(q)].add((d["votering_id"], d["parti"]))
    out = []
    for r in rows:
        phrase = _key(r["text"])
        klass = _class_of(r["slug"])
        pool = quotes.get(klass, {})
        catches, false_positives = [], []
        for q, votes in pool.items():
            if phrase in q or q in phrase:
                catches.append((q, votes))
                if stray := votes - r["votes"]:
                    false_positives.append((q, stray))
        elsewhere = [
            slug
            for slug, served in _served_by_class().get(klass, [])
            if slug != r["slug"] and phrase in served
        ]
        out.append(
            {
                "slug": r["slug"],
                "block_id": r["block_id"],
                "role": r["role"],
                "phrase": r["text"],
                "words": len(r["text"].split()),
                "chars": len(r["text"]),
                "decisions": len(r["votes"]),
                "catches": sum(len(v) for _, v in catches),
                "false_positives": sum(len(v) for _, v in false_positives),
                "false_positive_quotes": sorted(q for q, _ in false_positives)[:5],
                "other_documents": elsewhere,
            }
        )
    out.sort(key=lambda c: (-c["false_positives"], -len(c["other_documents"]), -c["decisions"]))
    return out


def _print_scope(title: str, scope: dict, total: dict, note: str = "") -> None:
    print(
        f"\n  {title}: {scope['blocks']} blocks / {scope['decisions']} distinct votes "
        f"({scope['block_decisions']} block-votes, "
        f"{_pct(scope['block_decisions'], total['block_decisions'])} of all block-votes)"
    )
    if note:
        print(f"    {note}")
    if scope["by_party"]:
        print(
            "    by party: "
            + " | ".join(f"{p} {a['decisions']}" for p, a in scope["by_party"].items())
        )
    for slug, a in scope["by_document"].items():
        print(
            f"    {slug:<26} {a['blocks']:>3} blocks  {a['decisions']:>5} votes  "
            f"{a['citations']:>5} citations"
        )
    for b in scope["blocks_listed"]:
        text = b["text"] if len(b["text"]) <= 60 else b["text"][:57] + "…"
        print(
            f"      {b['slug']:<26} {b['block_id']} {b['role']:<6} "
            f"{b['decisions']:>4} votes  {text!r}"
        )


def report(run_id: str, out: str | None = None, results_dir=None) -> dict:
    """Print the navigation-citation finding; optionally write it as JSON."""
    rows = cited_blocks(run_id, results_dir)
    rep = navigation_report(run_id, results_dir, rows=rows)
    rep["weak_list_candidates"] = weak_list_candidates(run_id, results_dir, rows=rows)
    t = rep["totals"]
    print(
        f"{run_id}: {t['blocks']} cited blocks, {t['citations']} citations, "
        f"{t['decisions']} distinct votes ({t['block_decisions']} block-votes)"
    )
    print("\n  cited blocks by role")
    for role, a in sorted(rep["by_role"].items(), key=lambda kv: -kv[1]["blocks"]):
        print(
            f"    {role:<10} {a['blocks']:>6} blocks  {a['citations']:>7} citations  "
            f"{a['block_decisions']:>7} block-votes"
        )
    _print_scope(
        "on a `label` or `toc` block (role alone — the plan's literal question)",
        rep["label_toc"],
        t,
        "NOT the finding: `label` is a typographic class, and in valmanifest-s / -l "
        "it is the party's own pledge list",
    )
    _print_scope(
        "on any navigational role (headings included, role alone)", rep["role_only"], t
    )
    _print_scope(
        "on navigation, role AND text (what the site marks)", rep["navigational"], t
    )
    cands = rep["weak_list_candidates"]
    clean = [c for c in cands if not c["false_positives"] and not c["other_documents"]]
    print(
        f"\n  as WEAK_LIST entries: {len(cands)} candidate phrases, "
        f"{len(clean)} clean on both tests (no committed false positive, and the "
        "phrase occurs in no other party's document of the class)"
    )
    for c in cands:
        text = c["phrase"] if len(c["phrase"]) <= 46 else c["phrase"][:43] + "…"
        print(
            f"    {c['words']:>2}w {c['chars']:>3}c  catches {c['catches']:>4}  "
            f"false {c['false_positives']:>4}  elsewhere {len(c['other_documents']):>2}  "
            f"{c['slug']:<20} {text!r}"
        )
        for q in c["false_positive_quotes"]:
            print(f"        would also mark: {q[:76]!r}")
        for slug in c["other_documents"]:
            print(f"        also occurs in: {slug}")
    if out:
        from pathlib import Path

        Path(out).write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {out}")
    return rep
