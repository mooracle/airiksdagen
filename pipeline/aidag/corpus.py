"""Which documents a party may see on a given date — the one place that decides.

The prompt builder, the citation repairer and the verify gate all ask this
module, so they cannot drift apart: if a document was never in context, a
citation to it is a memory leak and verify must fail it.

Two rules, both point-in-time:

  adoption gate   a document is visible only from its own adoption/submission
                  date onward. A party programme adopted in 2025 was written
                  after (and partly because of) the votes we reconstruct, so a
                  2023 vote must not see it — while a 2025 vote legitimately
                  should. Tidöavtalet already worked this way; p5 applies the
                  same rule to programmes and shadow budgets.

  own-motion gate a party voting on its OWN shadow budget must not be shown it.
                  That is the answer, not a source — the same reason reservation
                  authorship is scrubbed. Nine case x party pairs in the term.

p4 (the full-v2 corpus) is valmanifest + Tidöavtalet only, and is frozen: its
bytes are the exact inputs the committed decisions were generated from.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from aidag.config import (
    BUDGET_MOTIONS,
    CORPUS_DIR,
    PARTY_PROGRAMS,
    RAW_DIR,
    TIDO_DATE,
    TIDO_SIGNATORIES,
    TIDO_SUPPORT,
)

# Document kinds an agent may cite, per prompt version. The decision schema's
# enum is built from this — an agent can only name a document it was given.
DOCS_P4 = ("valmanifest", "tidoavtalet")
DOCS_P5 = ("valmanifest", "tidoavtalet", "partiprogram", "budgetmotion")


def docs_for_version(prompt_version: str) -> tuple[str, ...]:
    return DOCS_P5 if prompt_version >= "p5" else DOCS_P4


def tido_applies(code: str, datum: str) -> bool:
    return code in (TIDO_SIGNATORIES | TIDO_SUPPORT) and datum >= TIDO_DATE


def program_at(code: str, datum: str) -> dict | None:
    """The party's programme standing on `datum` — the latest one adopted by then."""
    standing = [v for v in PARTY_PROGRAMS.get(code, []) if v["from"] <= datum]
    return standing[-1] if standing else None


def budget_at(code: str, rm: str, datum: str) -> dict | None:
    """The party's shadow budget for this riksmöte, if already submitted."""
    b = BUDGET_MOTIONS.get((code, rm))
    if b and b["from"] <= datum:
        return b
    return None


@lru_cache(maxsize=1)
def _references_by_case() -> dict[str, set[str]]:
    """votering_id -> every dok_id the betänkande handles, from the RAW json.

    Deliberately NOT read from cases.parquet: extract_references() truncates at
    20 and 1,443 of 2,539 cases sit at that ceiling, so a party's own budget
    motion can silently fall off the list — and the exclusion would then leak it.
    """
    out: dict[str, set[str]] = {}
    for path in (RAW_DIR / "dokumentstatus").glob("*.json"):
        try:
            ds = json.loads(path.read_text())["dokumentstatus"]
        except Exception:
            continue
        refs = ds.get("dokreferens", {}).get("referens") or []
        if isinstance(refs, dict):
            refs = [refs]
        dok_ids = {r.get("ref_dok_id") for r in refs if r.get("ref_dok_id")}
        uf = ds.get("dokutskottsforslag", {}).get("utskottsforslag") or []
        if isinstance(uf, dict):
            uf = [uf]
        for u in uf:
            # raw json holds the id lowercased; cases.parquet uppercases it
            if vid := u.get("votering_id"):
                out.setdefault(vid.upper(), set()).update(dok_ids)
    return out


def budget_excluded(code: str, rm: str, votering_id: str, datum: str) -> bool:
    """True when this division is voting on the party's own shadow budget."""
    b = budget_at(code, rm, datum)
    if not b:
        return False
    return b["dok_id"] in _references_by_case().get(votering_id, set())


@lru_cache(maxsize=64)
def _text(filename: str) -> str:
    return (CORPUS_DIR / filename).read_text().lstrip("﻿").strip()


def documents_for(
    code: str, datum: str, rm: str, votering_id: str, prompt_version: str
) -> list[tuple[str, str, str]]:
    """(document_kind, xml_tag_body, text) for everything this party may see now.

    Ordered stable-prefix-first so the party corpus caches well: manifesto,
    programme, Tidöavtalet, then the budget (the only part that varies by case).
    """
    from aidag.fetch_corpus import budget_filename, program_filename

    docs: list[tuple[str, str, str]] = [
        ("valmanifest", "valmanifest_2022", _text(f"valmanifest-2022-{code.lower()}.txt"))
    ]
    if prompt_version < "p5":
        if tido_applies(code, datum):
            docs.append(("tidoavtalet", "tidoavtalet", _text("tidoavtalet-2022.txt")))
        return docs

    if prog := program_at(code, datum):
        docs.append((
            "partiprogram",
            f'partiprogram antaget="{prog["from"]}"',
            _text(program_filename(code, prog)),
        ))
    if tido_applies(code, datum):
        docs.append(("tidoavtalet", "tidoavtalet", _text("tidoavtalet-2022.txt")))
    if (b := budget_at(code, rm, datum)) and not budget_excluded(code, rm, votering_id, datum):
        docs.append((
            "budgetmotion",
            f'budgetmotion beteckning="{b["bet"]}"',
            _text(budget_filename(code, rm)),
        ))
    return docs


def context_key(code: str, datum: str, rm: str, votering_id: str, prompt_version: str) -> str:
    """Identity of a party's context. Cases sharing it share one system prompt,
    which is what lets grouped agents batch and the prompt cache hit."""
    if prompt_version < "p5":
        return f"{code}-{'tido' if tido_applies(code, datum) else 'base'}"
    prog = program_at(code, datum)
    parts = [code, prog["from"][:4] if prog else "noprog"]
    if tido_applies(code, datum):
        parts.append("tido")
    if (b := budget_at(code, rm, datum)) and not budget_excluded(code, rm, votering_id, datum):
        parts.append(b["bet"].replace("/", "").replace(":", "-"))
    return "-".join(parts)
