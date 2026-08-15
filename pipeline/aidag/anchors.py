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


def summarize(payload: dict) -> dict:
    """The inline aggregate: per block, and per anchor within it."""
    blocks: dict[str, dict] = {}
    for a in payload["anchors"]:
        b = blocks.setdefault(
            a["block_id"],
            {**_tally([]), "svag": 0, "tiers": Counter(), "parties": Counter(), "anchors": []},
        )
        tally = _tally(a["refs"])
        for k, n in tally.items():
            b[k] += n
        for r in a["refs"]:
            b["tiers"][r["tier"]] += 1
            b["parties"][r["parti"]] += 1
            b["svag"] += bool(r["svag"])
        b["anchors"].append({"offset": a["offset"], "length": a["length"], **tally})
    for b in blocks.values():
        b["tiers"] = dict(sorted(b["tiers"].items(), key=lambda kv: (kv[0] is None, kv[0])))
        b["parties"] = dict(sorted(b["parties"].items()))
    return {
        "slug": payload["slug"],
        "run_id": payload["run_id"],
        "totals": {
            "anchors": payload["n_anchors"],
            **_tally([r for a in payload["anchors"] for r in a["refs"]]),
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
