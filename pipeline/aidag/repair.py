"""Repair non-verbatim citation quotes by aligning them to the source text.

Models occasionally paraphrase instead of copying (measured ~2% of citations
on Sonnet). For each quote not found verbatim (whitespace-normalized) in the
cited document, find the best-matching span; if similarity >= 0.75 replace the
quote with the TRUE span and flag `citat_korrigerat` (kept visible on the
site). Below the threshold (or when the cited document was never in context)
the quote cannot be verified: we BLANK the `quote` field — preserving the raw
model text in `quote_ej_verifierad` for audit — keep the citation's document
and princip, and flag the decision `citat_ej_verifierat`. Blanking keeps
`verify simulate` green (it skips empty quotes) and stops the site from ever
presenting an unverifiable string as a verbatim source, while the flag and
sidecar keep the event fully on the record.
"""

from __future__ import annotations

import difflib
import json

from aidag.config import RESULTS_DIR
from aidag.promptgen import _corpus_text
from aidag.simulate import _normalize_ws

THRESHOLD = 0.75


def best_span(quote: str, source: str) -> tuple[str, float]:
    """Best-matching word window in `source` for `quote` (both raw strings)."""
    q = _normalize_ws(quote)
    src_words = _normalize_ws(source).split(" ")
    q_len = len(q.split(" "))
    best, best_ratio = "", 0.0
    for width in {max(3, q_len - 3), q_len, q_len + 3, q_len + 8}:
        if width > len(src_words):
            continue
        step = max(1, width // 4)
        for i in range(0, len(src_words) - width + 1, step):
            cand = " ".join(src_words[i : i + width])
            ratio = difflib.SequenceMatcher(None, q, cand, autojunk=False).ratio()
            if ratio > best_ratio:
                best, best_ratio = cand, ratio
                # refine around the hit
                for j in range(max(0, i - step), min(len(src_words) - width, i + step) + 1):
                    cand2 = " ".join(src_words[j : j + width])
                    r2 = difflib.SequenceMatcher(None, q, cand2, autojunk=False).ratio()
                    if r2 > best_ratio:
                        best, best_ratio = cand2, r2
    return best, best_ratio


def _flag(d: dict, flag: str) -> None:
    d.setdefault("flags", [])
    if flag not in d["flags"]:
        d["flags"].append(flag)


def _mark_unverifiable(d: dict, c: dict) -> None:
    """Quote couldn't be verified verbatim: stash the raw model text in
    `quote_ej_verifierad`, blank `quote` (so `verify simulate` and the site
    never treat it as a real source), and flag the decision."""
    if c.get("quote"):
        c["quote_ej_verifierad"] = c["quote"]
    c["quote"] = ""
    _flag(d, "citat_ej_verifierat")


def repair_decision(d: dict, corpus: dict[str, str]) -> tuple[int, int, int]:
    """Repair one decision's citations in place. Returns (ok, fixed, failed)."""
    norm = {k: _normalize_ws(v) for k, v in corpus.items()}
    n_ok = n_fixed = n_failed = 0
    for c in d.get("citations", []):
        if not c["quote"]:
            continue
        src = norm.get(c["document"])
        if src is None:
            # cited document was never in this agent's context (e.g. a
            # hallucinated name) — nothing to align against, blank it
            _mark_unverifiable(d, c)
            n_failed += 1
            continue
        if _normalize_ws(c["quote"]) in src:
            n_ok += 1
            continue
        span, ratio = best_span(c["quote"], corpus[c["document"]])
        if ratio >= THRESHOLD:
            c["quote"] = span
            _flag(d, "citat_korrigerat")
            n_fixed += 1
        else:
            _mark_unverifiable(d, c)
            n_failed += 1
    return n_ok, n_fixed, n_failed


def run(run_id: str) -> None:
    import polars as pl

    from aidag.config import PROCESSED_DIR
    from aidag.corpus import documents_for

    cases = pl.read_parquet(
        PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum", "rm"]
    )
    meta = {r["votering_id"]: (r["datum"], r["rm"]) for r in cases.iter_rows(named=True)}

    sim_dir = RESULTS_DIR / "simulations" / run_id
    n_ok = n_fixed = n_failed = 0
    for path in sorted(sim_dir.glob("*.jsonl")):
        party = path.stem
        out_lines = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            # align against exactly the documents this decision's agent was
            # served — under p5 that varies by date and by case
            datum, rm = meta.get(d["votering_id"], ("", ""))
            corpus = {
                kind: text
                for kind, _tag, text in documents_for(
                    party, datum, rm, d["votering_id"], d["prompt_version"]
                )
            }
            ok, fixed, failed = repair_decision(d, corpus)
            n_ok += ok
            n_fixed += fixed
            n_failed += failed
            out_lines.append(json.dumps(d, ensure_ascii=False))
        # atomic replace — these files are the committed scientific record,
        # never leave them truncated on an interrupt
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(out_lines) + "\n")
        tmp.replace(path)
    print(f"citations: {n_ok} verbatim, {n_fixed} repaired (flagged), {n_failed} unverifiable")
