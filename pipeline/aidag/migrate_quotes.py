"""Move committed citation quotes onto the re-extracted corpus.

The quotes in `data/results/simulations/full-v4` were copied out of the flat
`pdf_to_text()` corpus. Structured extraction replaced 23 of those documents, and
while it is a no-op on most prose it does close the character damage the old
extractor left — `hälso och` becomes `hälso- och`, `storregio nala` becomes
`storregionala`. A quote carrying the old spelling is no longer a substring of
what p6 now serves, so `verify simulate` fails it: 639 citations of 77,718.

Those citations were never wrong. The corpus moved under them, and that is a
different event from the one `repair-citations` records — `citat_korrigerat`
attributes the mismatch to the model paraphrasing. This pass exists so the two
stay distinguishable in the record:

    citat_migrerat      we rewrote the quote because the corpus was re-extracted;
                        the exact wording the agent produced is kept beside it in
                        `quote_fore_migrering`
    citat_ej_migrerat   we could not place it, so we left it exactly as the agent
                        wrote it. Never blanked here — blanking is
                        `repair-citations`' decision to make and its sidecar to
                        write.

A failure is therefore not a dead end; it is a handoff. The 12 quotes in
`data/corpus/known-unrecovered.json` land here by design, and the repair pass
that follows resolves them the way it resolves any other unverifiable quote.

Those 12 are refused *before* the fuzzy matcher rather than after it. Task 3
measured them as reading order the geometry does not determine — a heading that
belongs to a figure sitting inside a two-column text block — and a matcher that
scores word windows cannot see the difference between that and a typo. It scores
all of them over the 0.75 bar and produces exactly what you would expect it to:

    Det handlar också om att renodla polisens uppdrag – ...
    Valmanifest 2022 renodla polisens uppdrag – ...            (0.878)

    Sverige har redan ett av världens högsta skattetryck ...
    2022 har redan ett av världens högsta skattetryck ...      (0.932)

The rotated margin stamp and the chart source line are verbatim in the served
document, so those rewrites verify; they are still not what the party wrote and
not what the agent quoted. Consulting a list of known-unrecoverable quotes and
then overruling it is not consulting it, so a listed quote is left exactly as the
agent wrote it and flagged.

Locating, in order:

  exact     whitespace-collapsed substring of the served document. This is the
            same test `verify simulate` applies, so "exact" here means "already
            green" — nothing is touched.

  fuzzy     `repair.best_span` over candidate windows chosen by an inverted token
            index. Whole-document `best_span` does not finish in reasonable time
            over a 172-page programme; the prefilter takes it to a fraction of a
            second per quote. Candidates are blocks — a served document holds one
            block per line (see `extract_corpus.text_from_blocks`), so the lines
            ARE the blocks and the window is a slice of the served text rather
            than a re-join of block text. That is what makes the accepted span a
            substring of the served document by construction, and it is checked
            again before the rewrite is accepted.

Which document a citation means is a question about the decision's date: the
record stores only the class (`partiprogram`), and L has three editions, V/KD/S/
SD/MP two each. Resolution goes through `corpus.program_at()`, the same
point-in-time gate `documents_for()` uses, so a KD vote in 2023 migrates against
`partiprogram-kd-2015` and never against `-2025`.

Below p6 nothing is migrated at all: those runs read `data/corpus/frozen/`, whose
bytes did not move.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from aidag.config import CORPUS_DIR, RESULTS_DIR, version_ge
from aidag.repair import _flag, best_span
from aidag.simulate import _normalize_ws

# Same bar as `repair.THRESHOLD`, deliberately: a span good enough to stand as
# the agent's quote after a paraphrase is good enough after a re-extraction, and
# two thresholds for one judgement would only drift apart.
THRESHOLD = 0.75

# The first p6 version. Older runs verify against the frozen corpus.
MIGRATED_FROM = "p6"

# The document classes re-extracted into blocks, and so the only ones a slug can
# be resolved for. Named rather than inlined because `anchors.collect()` has to
# tell "this class was never in scope" apart from "this class is in scope and
# the slug still did not resolve" — the second is a defect and the first is not.
MIGRATED_CLASSES = frozenset({"valmanifest", "partiprogram"})

# Prefilter shape. The rarest tokens of a quote carry nearly all of its locating
# power — a candidate ranked by how many of them it holds puts the true block
# first almost always, and the window either side of it absorbs the rest.
RARE_TOKENS = 12
MAX_CANDIDATES = 12
WINDOW = 1

_TOKEN = re.compile(r"\w+", re.UNICODE)


@dataclass(slots=True)
class Match:
    """Where a quote landed. `text` is empty unless the quote should be rewritten."""

    how: str  # exact | fuzzy | failed
    text: str
    ratio: float


# The verdict for an allowlisted quote. Ratio 0.0 rather than the score the
# matcher would have given it: the number would read as "nearly good enough" for
# a span this pass has decided not to use.
KNOWN_FAILURE = Match("failed", "", 0.0)


def _ratio(q: str, cand: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, q, cand, autojunk=False).ratio()


def _refine(quote: str, window: str) -> tuple[str, float]:
    """`best_span`, then hill-climb its edges one word at a time.

    `best_span` scores fixed-width word windows, and a repair that closes a
    broken word makes the true span one word SHORTER than the quote that is
    looking for it. The width it settles on therefore overshoots, and the
    overshoot is a real word off the neighbouring block:

        Vi vill se ett robust samhälle ...        (quote)
        relationer Vi vill se ett robust ...      (best_span, width fixed)

    Moving either edge in or out while the ratio strictly improves drops the
    bleed and picks up anything the width could not reach. It is a strict
    improvement by construction — the loop only ever takes a better score — and
    it costs a handful of comparisons against a span that is about to be written
    into the research record.
    """
    span, ratio = best_span(quote, window)
    words = window.split()
    pos = window.find(span)
    if not span or pos < 0:
        return span, ratio
    i = len(window[:pos].split())
    j = i + len(span.split())
    moved = True
    while moved:
        moved = False
        for ni, nj in ((i + 1, j), (i - 1, j), (i, j - 1), (i, j + 1)):
            if ni < 0 or nj > len(words) or nj - ni < 1:
                continue
            cand = " ".join(words[ni:nj])
            r = _ratio(quote, cand)
            if r > ratio:
                i, j, span, ratio, moved = ni, nj, cand, r, True
    return span, ratio


@dataclass(slots=True)
class DocIndex:
    """One served document, indexed for candidate lookup.

    `served` is the whitespace-collapsed text `verify simulate` compares against,
    so a span sliced out of it verifies by construction. `spans` are the offsets
    of each block within it, in reading order.
    """

    slug: str
    served: str
    spans: list[tuple[int, int]]
    postings: dict[str, list[int]] = field(default_factory=dict)

    def window(self, i: int) -> tuple[int, int]:
        lo = self.spans[max(0, i - WINDOW)][0]
        hi = self.spans[min(len(self.spans) - 1, i + WINDOW)][1]
        return lo, hi

    def candidates(self, quote: str) -> list[int]:
        """Block indices most likely to hold the quote, rarest tokens first."""
        toks = {t for t in _TOKEN.findall(quote.lower()) if t in self.postings}
        # the token itself breaks ties: `toks` is a set, so equally-rare tokens
        # would otherwise be ordered by PYTHONHASHSEED and the [:RARE_TOKENS] cut
        # would select a different candidate set run to run — this pass rewrites
        # the research record and has to be reproducible
        ranked = sorted(toks, key=lambda t: (len(self.postings[t]), t))[:RARE_TOKENS]
        score: Counter[int] = Counter()
        for t in ranked:
            score.update(self.postings[t])
        return [i for i, _ in score.most_common(MAX_CANDIDATES)]

    def locate(self, quote: str) -> Match:
        q = _normalize_ws(quote)
        if q and q in self.served:
            return Match("exact", "", 1.0)
        windows = {self.window(i) for i in self.candidates(q)}
        if not windows:
            # Not one word of the quote occurs anywhere in the document (an empty
            # quote reaches this too), so no span of it can match. Falling back to
            # the whole served text would spend the multi-minute full-document
            # scan this module exists to avoid — see the module docstring's
            # 172-page measurement — to arrive at exactly this verdict.
            return Match("failed", "", 0.0)
        best, ratio = "", 0.0
        for lo, hi in sorted(windows):
            span, r = _refine(q, self.served[lo:hi])
            if r > ratio:
                best, ratio = span, r
        # a span is a slice of the served text, so this holds — but the rewrite
        # is the research record, and an assumption that has never been checked
        # is the one that fails silently.
        if ratio >= THRESHOLD and _normalize_ws(best) in self.served:
            return Match("fuzzy", best, ratio)
        return Match("failed", "", ratio)


@lru_cache(maxsize=64)
def index_for(slug: str) -> DocIndex:
    """Build (once) the served text and block offsets for one document.

    Blocks are recovered as the lines of the served document rather than from
    `blocks/<slug>.json`: `normalize()` drops blank lines and page furniture, so
    the lines that survive are exactly the blocks that are served, already in
    reading order. Reading the block file instead would reintroduce the drop set
    as something this module has to model.
    """
    from aidag.corpus import _text

    lines = _text(f"{slug}.txt", clean=True).splitlines()
    served_parts, spans, pos = [], [], 0
    for line in lines:
        text = _normalize_ws(line)
        if not text:
            continue
        if served_parts:
            pos += 1  # the space `_normalize_ws` puts between two lines
        spans.append((pos, pos + len(text)))
        served_parts.append(text)
        pos += len(text)
    served = " ".join(served_parts)
    postings: dict[str, list[int]] = defaultdict(list)
    for i, (lo, hi) in enumerate(spans):
        for tok in set(_TOKEN.findall(served[lo:hi].lower())):
            postings[tok].append(i)
    return DocIndex(slug=slug, served=served, spans=spans, postings=dict(postings))


def resolve_slug(kind: str, code: str, datum: str) -> str | None:
    """The document slug a citation of `kind` by `code` on `datum` refers to.

    None when the class is outside this migration: budgetmotioner and Tidöavtalet
    were not re-extracted, so their quotes still match, and a class no version
    serves is not this pass's to judge.
    """
    from aidag.corpus import program_at

    if kind == "valmanifest":
        slug = f"valmanifest-2022-{code.lower()}"
        return slug if (CORPUS_DIR / f"{slug}.txt").exists() else None
    if kind == "partiprogram":
        prog = program_at(code, datum)
        return f"partiprogram-{code.lower()}-{prog['from'][:4]}" if prog else None
    return None


def known_unrecovered() -> set[tuple[str, str]]:
    """(slug, whitespace-collapsed quote) pairs Task 3 measured as unrecoverable.

    Both a report label — an accepted failure against a new one — and a decision:
    a listed quote is never fuzzy-rewritten, because what a matcher finds for it
    is a neighbouring column with page furniture spliced in (see the module
    docstring). `tests/test_docx.py::TestKnownUnrecovered` holds the list to a
    ratchet against the *extraction*, so short-circuiting here cannot hide a
    document that starts extracting correctly.
    """
    path = CORPUS_DIR / "known-unrecovered.json"
    if not path.exists():
        return set()
    return {
        (q["document"], _normalize_ws(q["quote"]))
        for q in json.loads(path.read_text())["quotes"]
    }


def migrate_decision(
    d: dict,
    datum: str,
    cache: dict[tuple[str, str], Match] | None = None,
    failures: Counter | None = None,
    known: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset(),
) -> Counter:
    """Migrate one decision's citations in place. Returns a counter of outcomes.

    `cache` memoizes the locator by (slug, quote) — 77,718 citations carry 16,723
    distinct quotes and 58% of them are cited more than once. `failures` tallies
    unplaced citations by (slug, quote) so the report can weigh them against
    `known-unrecovered.json` without a second pass over the rewritten files.
    `known` is that allowlist: a listed quote fails without being offered to the
    matcher at all.

    Flags are derived, not accumulated: `citat_migrerat` follows the presence of
    a sidecar and `citat_ej_migrerat` follows this pass's own failures, so a
    second run over a partly-migrated file converges instead of freezing the
    first run's verdict.
    """
    cache = {} if cache is None else cache
    out: Counter = Counter()
    failed = False
    for c in d.get("citations", []):
        quote = c.get("quote") or ""
        if not quote:
            # already blanked by repair-citations: the raw text lives in
            # `quote_ej_verifierad` and is not a claim about the corpus
            out["blank"] += 1
            continue
        slug = resolve_slug(c["document"], d["parti"], datum)
        if slug is None:
            out["out_of_scope"] += 1
            continue
        key = (slug, _normalize_ws(quote))
        if key in known:
            m = KNOWN_FAILURE
        else:
            if key not in cache:
                cache[key] = index_for(slug).locate(quote)
            m = cache[key]
        out[m.how] += 1
        out[f"{m.how}:{slug}"] += 1
        if m.how == "fuzzy":
            c.setdefault("quote_fore_migrering", quote)
            c["quote"] = m.text
        elif m.how == "failed":
            failed = True
            if failures is not None:
                failures[key] += 1
    if any("quote_fore_migrering" in c for c in d.get("citations", [])):
        _flag(d, "citat_migrerat")
    flags = d.get("flags")
    if failed:
        _flag(d, "citat_ej_migrerat")
    elif flags and "citat_ej_migrerat" in flags:
        flags.remove("citat_ej_migrerat")
    return out


def _report(counts: Counter, unknown: list[tuple[str, str]], dry_run: bool) -> None:
    docs = sorted({k.split(":", 1)[1] for k in counts if ":" in k})
    for slug in docs:
        row = {how: counts[f"{how}:{slug}"] for how in ("exact", "fuzzy", "failed")}
        if row["fuzzy"] or row["failed"]:
            print(
                f"  {slug}: {row['exact']} exact, {row['fuzzy']} migrated, "
                f"{row['failed']} unplaced"
            )
    if unknown:
        print(f"  {len(unknown)} unplaced quote(s) NOT on the known-unrecovered list:")
        for slug, q in unknown[:20]:
            print(f"    {slug}: {q[:90]}")
        if len(unknown) > 20:
            print(f"    ... and {len(unknown) - 20} more")
    print(
        f"citations: {counts['exact']} already exact, {counts['fuzzy']} migrated, "
        f"{counts['failed']} unplaced ({counts['known_failed']} known, "
        f"{counts['failed'] - counts['known_failed']} new), {counts['blank']} blank, "
        f"{counts['out_of_scope']} out of scope, {counts['pre_p6']} pre-p6 decisions skipped"
        + (" [dry run — nothing written]" if dry_run else "")
    )


def run(run_id: str, dry_run: bool = False) -> Counter:
    import polars as pl

    from aidag.config import PROCESSED_DIR

    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum"])
    datum_by_vid = {r["votering_id"]: r["datum"] for r in cases.iter_rows(named=True)}

    sim_dir = RESULTS_DIR / "simulations" / run_id
    known = known_unrecovered()
    counts: Counter = Counter()
    cache: dict[tuple[str, str], Match] = {}
    failures: Counter = Counter()
    for path in sorted(sim_dir.glob("*.jsonl")):
        out_lines = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if not version_ge(d["prompt_version"], MIGRATED_FROM):
                # pre-p6 runs read the frozen corpus, which this change did not move
                counts["pre_p6"] += 1
            else:
                counts.update(
                    migrate_decision(
                        d, datum_by_vid.get(d["votering_id"], ""), cache, failures, known
                    )
                )
            out_lines.append(json.dumps(d, ensure_ascii=False))
        if not dry_run:
            # atomic replace — these files are the committed scientific record,
            # never leave them truncated on an interrupt
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(out_lines) + "\n")
            tmp.replace(path)
    counts["known_failed"] = sum(n for key, n in failures.items() if key in known)
    unknown = sorted(key for key in failures if key not in known)
    _report(counts, unknown, dry_run)
    return counts
