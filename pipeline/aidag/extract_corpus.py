"""Regenerate the 23 cited party documents from their cached source PDFs.

`fetch_corpus` downloads and flattens; this module re-reads the same cached
bytes through `docx` and writes two artifacts per document:

    data/corpus/blocks/<slug>.json   blocks with ids, roles, page and face
    data/corpus/<slug>.txt           DERIVED from those blocks

The .txt is derived rather than written independently, and that is the whole
point: text and structure cannot drift if one is a function of the other. What
agents read, what `verify simulate` checks and what the site renders are then
three views of one extraction instead of three extractions.

Scope is the 23 documents citations actually reach — 8 valmanifest, 15
partiprogram. The 16 budgetmotioner come from Riksdagen's HTML and Tidöavtalet's
text is the frozen p4 extraction; neither has a source PDF this path can read,
and neither is touched.

Two interlocks, because this overwrites the committed research record:

  frozen first   a .txt is never rewritten unless `data/corpus/frozen/` already
                 holds the bytes that stood before. Everything below p6 reads
                 that directory (see corpus._text), so an unfrozen overwrite
                 would silently invalidate full-v2's and full-v3's citations.

  equivalence    the 8 manifestos change *source rendition* here — SND serves
                 the same document as /txt and as /pdf, and only the PDF carries
                 the font metrics. A rendition can legitimately differ in line
                 breaks; it cannot legitimately differ in how much text it has,
                 so the prose word count must land within WORD_TOLERANCE of the
                 text rendition or the document is left alone and the run fails.
"""

from __future__ import annotations

import json
import re

from aidag import docx
from aidag.config import (
    BLOCKS_DIR,
    CORPUS_DIR,
    FROZEN_DIR,
    MANIFESTO_PDF_URL,
    PARTIES,
    PARTY_CODES,
    PARTY_PROGRAMS,
    SND_TXT_URL,
)

# How far the PDF rendition's prose word count may sit from the text
# rendition's. Only the 8 manifestos have two renditions to compare; the
# programmes are the same bytes read by a better extractor, so the guard would
# be measuring this module against itself.
WORD_TOLERANCE = 0.05

# Roles excluded from that count. The PDF exposes what a text rendition throws
# away — chart axis ticks, figure sources, valmanifest-m's 39 rotated
# 'Valmanifest 2022' margin stamps — and counting them measures the charts
# rather than the document. valmanifest-m carries 753 such words and reads 1.061
# against its text rendition on the raw count; on prose it reads 0.995, and the
# other seven manifestos, which have almost no chart furniture, all land inside
# 1.2% either way. A tolerance sized by one document's infographics would be too
# slack to catch the thing this guard exists for: a /pdf endpoint serving a
# different, shorter document than /txt.
NON_PROSE_ROLES = ("caption", "unreadable")

_HEADER = re.compile(r"^<!--.*?-->\n", flags=re.S)


def manifesto_slugs() -> list[str]:
    return [f"valmanifest-2022-{code.lower()}" for code in PARTY_CODES]


def program_slugs() -> list[str]:
    return [
        f"partiprogram-{code.lower()}-{v['from'][:4]}"
        for code, versions in PARTY_PROGRAMS.items()
        for v in versions
    ]


def cited_slugs() -> list[str]:
    """The 23 documents p6 can cite — the exact scope of this extraction."""
    return manifesto_slugs() + program_slugs()


def frozen_text(slug: str) -> str:
    """The document as it stood before structured extraction."""
    return (FROZEN_DIR / f"{slug}.txt").read_text(encoding="utf-8").lstrip("﻿")


def _program_version(slug: str) -> dict | None:
    for code, versions in PARTY_PROGRAMS.items():
        for v in versions:
            if slug == f"partiprogram-{code.lower()}-{v['from'][:4]}":
                return v
    return None


def provenance(slug: str, source: str) -> str:
    """The `<!-- ... -->` credit line, carried through to the site page.

    Programmes keep the header `fetch_corpus` already wrote — same document,
    same adoption date, same URL, only a better extractor. The manifestos gain
    one, because for them this is where the rendition changed and a corpus that
    does not say which rendition it holds is a corpus that cannot be checked.
    """
    if v := _program_version(slug):
        return f"<!-- {v['title']} | antaget {v['from']} | {v['url']} -->"
    code = slug.rsplit("-", 1)[-1]
    party = PARTIES[code.upper()]["name"]
    if source == "text":
        # no text layer in the PDF; the blocks come from SND's text rendition
        return (
            f"<!-- Valmanifest 2022 ({party}) | SND Vivill, textrendition "
            f"(PDF utan textlager) | {SND_TXT_URL.format(code=code)} -->"
        )
    return (
        f"<!-- Valmanifest 2022 ({party}) | SND Vivill, PDF-rendition | "
        f"{MANIFESTO_PDF_URL.format(code=code)} -->"
    )


def text_from_blocks(blocks: list[docx.Block], header: str) -> str:
    """The serialized corpus document: provenance, then one block per paragraph.

    Blocks are separated by a blank line so the file reads as prose, and each
    block is a single line so `corpus.normalize()` — which drops blank lines —
    maps one block to one line. That is what makes the round-trip assertable.
    """
    body = "\n\n".join(b.text for b in blocks)
    return f"{header}\n{body}\n"


def normalize_drops(text: str) -> bool:
    """True when `corpus.normalize()` will remove this block from the served text.

    The documented drop set, and the whole of it: bare page numbers and rules,
    and lines of undecodable display-font glyphs (mp-2013's 50 section headings,
    kept in the blocks as role `unreadable` so the reader is told a heading is
    there rather than being shown nothing).
    """
    return bool(re.fullmatch(r"[\d\s.,%‑‒–—•·|-]+", text)) or docx._broken_font_line(text)


def extract_one(slug: str) -> tuple[list[docx.Block], dict]:
    """Blocks for one slug, from its cached PDF or — failing that — its text."""
    from aidag.fetch_corpus import source_pdf_bytes

    try:
        ex = docx.extract(source_pdf_bytes(slug))
        return ex.blocks, {
            "source": "pdf",
            "pages": ex.pages,
            "dropped": ex.dropped,
        }
    except docx.NoTextLayer as e:
        # valmanifest-2022-c: 38 pages of vector outlines. Its committed text is
        # SND's plain-text rendition, one paragraph per line, so that is what the
        # blocks are built from — there is no font metric to recover.
        return blocks_from_frozen(slug), {
            "source": "text",
            "pages": e.pages,
            "dropped": [],
            "note": str(e),
        }


def blocks_from_frozen(slug: str) -> list[docx.Block]:
    return docx.blocks_from_text(frozen_text(slug))


def prose_words(blocks: list[docx.Block]) -> int:
    return sum(len(b.text.split()) for b in blocks if b.role not in NON_PROSE_ROLES)


def check_equivalence(slug: str, blocks: list[docx.Block]) -> float:
    """Prose word ratio of the new extraction to the frozen one; raises if off.

    Applied to the manifestos only — see WORD_TOLERANCE. A ratio outside the
    band means the /pdf and /txt endpoints are not serving the same document,
    which is a source problem and not something a text-cleanup pass may paper
    over.
    """
    before = len(_HEADER.sub("", frozen_text(slug)).split())
    after = prose_words(blocks)
    ratio = after / before if before else 0.0
    if abs(ratio - 1.0) > WORD_TOLERANCE:
        raise ValueError(
            f"{slug}: PDF rendition has {after} prose words against {before} in the "
            f"text rendition (ratio {ratio:.3f}, tolerance ±{WORD_TOLERANCE:.0%}) — "
            "the two SND renditions are not the same document"
        )
    return ratio


def blocks_path(slug: str):
    return BLOCKS_DIR / f"{slug}.json"


def extract_slug(slug: str) -> dict:
    """Extract one document and write both artifacts. Returns a report row."""
    if not (FROZEN_DIR / f"{slug}.txt").exists():
        raise FileNotFoundError(
            f"{slug}: no frozen copy at {FROZEN_DIR / f'{slug}.txt'} — the pre-p6 "
            "bytes must be preserved before the corpus file is rewritten"
        )
    blocks, meta = extract_one(slug)
    if not blocks:
        raise ValueError(f"{slug}: extraction produced no blocks")

    header = provenance(slug, meta["source"])
    text = text_from_blocks(blocks, header)
    ratio = check_equivalence(slug, blocks) if slug in set(manifesto_slugs()) else None

    BLOCKS_DIR.mkdir(parents=True, exist_ok=True)
    blocks_path(slug).write_text(
        json.dumps(
            {"slug": slug, **meta, "blocks": [b.to_dict() for b in blocks]},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    (CORPUS_DIR / f"{slug}.txt").write_text(text, encoding="utf-8")

    roles: dict[str, int] = {}
    for b in blocks:
        roles[b.role] = roles.get(b.role, 0) + 1
    return {
        "slug": slug,
        "source": meta["source"],
        "pages": meta["pages"],
        "blocks": len(blocks),
        "words": len(_HEADER.sub("", text).split()),
        "ratio": ratio,
        "dropped": len(meta["dropped"]),
        "roles": roles,
    }


def run(slug: str | None = None, force: bool = False) -> list[dict]:
    slugs = [slug] if slug else cited_slugs()
    if slug and slug not in cited_slugs():
        raise ValueError(f"{slug}: not one of the {len(cited_slugs())} cited documents")
    report = []
    for s in slugs:
        if blocks_path(s).exists() and not force:
            print(f"  {s}: blocks exist, skipping (--force to re-extract)")
            continue
        row = extract_slug(s)
        ratio = f", {row['ratio']:.3f}x words" if row["ratio"] is not None else ""
        print(
            f"  {row['slug']}: {row['blocks']} blocks, {row['words']} words, "
            f"{row['dropped']} furniture lines dropped ({row['source']}{ratio})"
        )
        report.append(row)
    return report
