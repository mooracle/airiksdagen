"""Repair non-verbatim citation quotes by aligning them to the source text.

Models occasionally paraphrase instead of copying (measured ~2% of citations
on Sonnet). For each quote not found verbatim (whitespace-normalized) in the
cited document, find the best-matching span; if similarity >= 0.75 replace the
quote with the TRUE span and flag `citat_korrigerat` (kept visible on the
site). Below the threshold the quote is left as-is and flagged
`citat_ej_verifierat` — `verify simulate` counts only those as failures.
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


def run(run_id: str) -> None:
    sim_dir = RESULTS_DIR / "simulations" / run_id
    n_ok = n_fixed = n_failed = 0
    for path in sorted(sim_dir.glob("*.jsonl")):
        party = path.stem
        corpus = {
            "valmanifest": _corpus_text(f"valmanifest-2022-{party.lower()}.txt"),
            "tidoavtalet": _corpus_text("tidoavtalet-2022.txt"),
        }
        norm = {k: _normalize_ws(v) for k, v in corpus.items()}
        out_lines = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            for c in d.get("citations", []):
                src = norm.get(c["document"])
                if src is None or not c["quote"]:
                    continue
                if _normalize_ws(c["quote"]) in src:
                    n_ok += 1
                    continue
                span, ratio = best_span(c["quote"], corpus[c["document"]])
                if ratio >= THRESHOLD:
                    c["quote"] = span
                    d.setdefault("flags", [])
                    if "citat_korrigerat" not in d["flags"]:
                        d["flags"].append("citat_korrigerat")
                    n_fixed += 1
                else:
                    d.setdefault("flags", [])
                    if "citat_ej_verifierat" not in d["flags"]:
                        d["flags"].append("citat_ej_verifierat")
                    n_failed += 1
            out_lines.append(json.dumps(d, ensure_ascii=False))
        path.write_text("\n".join(out_lines) + "\n")
    print(f"citations: {n_ok} verbatim, {n_fixed} repaired (flagged), {n_failed} unverifiable")
