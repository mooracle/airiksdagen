"""Structured extraction: a party PDF into blocks with stable ids.

`fetch_corpus.pdf_to_text()` flattens a document to lines and throws away the
three things that say what a line *is* — font size, weight and where it sits on
the page. The site then tries to guess them back from line lengths, which is why
`partiprogram-m-2021` renders 187 false subheadings and `valmanifest-2022-v`
renders none at all for 51k characters.

This module keeps the metrics instead. The order is load-bearing:

    read_lines  ->  strip_running  ->  split_blocks  ->  assign_roles

Furniture goes first: a page number stripped after blocking has already landed
inside a block, and blocks are what citations anchor to. Roles go last, because
a role is a statement about a block relative to the rest of the document (see
`assign_roles`) and cannot be decided line by line.

No model runs anywhere in here. A paraphrased party programme would be a
credibility failure for this project, so structure comes from font metrics and
geometry or it does not come at all.
"""

from __future__ import annotations

import io
import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from aidag.corpus import _LIGATURES, _broken_font_line

# Under this many extracted characters per page a PDF has no usable text layer:
# the words are vector outlines. Only `valmanifest-2022-c` is in that state (38
# pages, 10 chars/page); the next lowest in the corpus is `valmanifest-2022-kd`
# at 946, so the floor sits in a two-orders-of-magnitude gap and does not need
# to be delicate.
MIN_CHARS_PER_PAGE = 200

# Furniture band: a line is a header/footer candidate only in the top or bottom
# tenth of the page. Chapter titles legitimately start inside the top band (V's
# 30pt titles sit at y=33 of 595), which is why band membership alone never
# drops a line — it must also repeat across pages.
BAND = 0.10
REPEAT_FRACTION = 0.20
REPEAT_FLOOR = 3

# Block splitting, in multiples of the document's median leading.
GAP_SPLIT = 1.65        # a vertical jump this big is a new block anywhere
LAYOUT_GAP_SPLIT = 1.20  # ...or this big when PyMuPDF also reports a new layout block
HEADING_RATIO = 1.12     # provisional; Task 3 replaces size ratios with style clusters

_DIGITS = re.compile(r"\d+")
_BULLET = re.compile(r"^(?:[•·▪◦‣⁃]|[–—-]\s|\(?\d{1,2}[.)]\s|[a-zA-ZåäöÅÄÖ][.)]\s)")
_LEADER = re.compile(r"\.{4,}")
_PAGE_NUMBER = re.compile(r"[\d\s.,%‑‒–—•·|ivxlcdmIVXLCDM-]+")
_SENTENCE_END = re.compile(r"[.!?:;][\"'”’)\]]?$")
_TOC_TAIL = re.compile(r"\S\s+\d{1,3}$")

# A line-final hyphen before a lowercase word is a wrap, except when the word is
# one of these: "el- och drivmedelspriser" breaks after a *suspended* hyphen that
# belongs to a compound, and joining it produces "eloch".
_SUSPENDED_NEXT = ("och", "eller", "samt", "respektive")


class NoTextLayer(Exception):
    """The PDF's words are drawn as outlines — there is nothing to extract.

    Carries the measurement so the caller can log why it fell back rather than
    reporting an opaque failure.
    """

    def __init__(self, pages: int, chars: int) -> None:
        self.pages = pages
        self.chars = chars
        per_page = chars / pages if pages else 0
        super().__init__(
            f"no usable text layer: {chars} chars over {pages} pages "
            f"({per_page:.0f}/page, floor {MIN_CHARS_PER_PAGE})"
        )


@dataclass(slots=True)
class Line:
    """One typeset line, with the metrics `pdf_to_text()` discards.

    `lb` is PyMuPDF's layout-block index within the page. It is a hint, not a
    paragraph: KD's programme puts a whole page in one layout block while L's
    2023 programme emits one per line, so `split_blocks` treats it as evidence
    rather than as a boundary.
    """

    page: int
    lb: int
    text: str
    size: float
    font: str
    bold: bool
    x0: float
    y0: float
    x1: float
    y1: float
    page_w: float
    page_h: float
    leader: bool = False


@dataclass(slots=True)
class Block:
    """A run of lines that reads as one unit, and the anchor a citation lands on."""

    id: str
    role: str
    page: int
    size: float
    bold: bool
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Extraction:
    blocks: list[Block]
    pages: int
    dropped: list[str] = field(default_factory=list)


def clean_line(s: str) -> str:
    """Character-level repair of one line, keeping the soft hyphen.

    The soft hyphen (U+00AD) survives deliberately: it marks where the typesetter
    broke a word, and `join_lines` needs it to close the break without a space.
    Stripping it here — which is what `pdf_to_text()` does — leaves 'Kristde' and
    'mokraterna' on separate lines with nothing to say they were ever one word.
    """
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_LIGATURES)                     # fi/fl/ff display glyphs
    s = s.replace("​", "").replace("﻿", "")
    s = s.replace("\xa0", " ")                      # nbsp, incl. L-2023's heading tabs
    s = re.sub(r"(?<=\w)¬(?=\w)", "", s)            # NOT SIGN standing in for a hyphen
    s = _LEADER.sub(" ", s)                         # table-of-contents dot leaders
    s = re.sub(r"[ \t\r\n\v\f]+", " ", s)           # KD's spans carry literal newlines
    return s.strip()


def read_lines(pdf_bytes: bytes) -> list[Line]:
    """Every text line in the document, in PyMuPDF's sorted reading order.

    Raises NoTextLayer when the document is vector outlines rather than text.
    """
    import fitz  # PyMuPDF

    out: list[Line] = []
    raw_chars = 0
    with fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf") as doc:
        pages = doc.page_count
        for pno, page in enumerate(doc):
            w, h = page.rect.width, page.rect.height
            for lb, blk in enumerate(page.get_text("dict", sort=True)["blocks"]):
                if blk.get("type") != 0:            # image
                    continue
                for ln in blk["lines"]:
                    spans = ln["spans"]
                    if not spans:
                        continue
                    raw = "".join(s["text"] for s in spans)
                    raw_chars += len(raw)
                    text = clean_line(raw)
                    if not text:
                        continue
                    # style from the span carrying most of the line: a heading
                    # that opens with a drop cap must not be typed by the cap.
                    sp = max(spans, key=lambda s: len(s["text"]))
                    x0, y0, x1, y1 = ln["bbox"]
                    out.append(
                        Line(
                            page=pno,
                            lb=lb,
                            text=text,
                            size=round(sp["size"], 1),
                            font=sp["font"],
                            bold=_is_bold(sp),
                            x0=x0, y0=y0, x1=x1, y1=y1,
                            page_w=w, page_h=h,
                            leader=bool(_LEADER.search(raw)),
                        )
                    )
    if pages and raw_chars / pages < MIN_CHARS_PER_PAGE:
        raise NoTextLayer(pages, raw_chars)
    return out


def _is_bold(span: dict) -> bool:
    """PyMuPDF's bold flag, with a name fallback for fonts that do not set it."""
    if span.get("flags", 0) & 2**4:
        return True
    name = span.get("font", "").lower()
    return any(w in name for w in ("bold", "black", "heavy", "extrabol", "semibol"))


def _signature(line: Line) -> str:
    """Digit-normalized text plus style: 'Sida 14' and 'Sida 15' are one signature.

    Style is part of it because text alone loses prose. V-2024 sets a 6pt running
    header carrying the chapter name, and the chapter's own 30pt title repeats
    that name once at the head of the chapter — in the same band. On text alone
    the title inherits the header's 19-page count and is dropped as furniture.
    """
    return f"{line.size}|{line.bold}|{_DIGITS.sub('#', line.text).strip()}"


def _in_band(line: Line) -> bool:
    return line.y0 < BAND * line.page_h or line.y1 > (1 - BAND) * line.page_h


def strip_running(lines: list[Line]) -> tuple[list[Line], list[Line]]:
    """Split off running headers, footers and page numbers. Returns (kept, dropped).

    Two conditions, both required, because either alone loses prose:

      position   only the top/bottom tenth of the page is furniture territory
      repetition the digit-normalized text must recur on max(3, 20% of pages)

    Position alone would drop V-2024's 30pt chapter titles, which sit at y=33 on
    a 595pt page. Repetition alone would drop 'Moderaterna kommer att:', which
    genuinely opens seven different pages of the manifesto mid-page.

    `dropped` is returned rather than logged so the corpus-wide invariant ("no
    block contains stripped furniture") can be asserted against it.
    """
    if not lines:
        return [], []
    n_pages = len({line.page for line in lines})
    threshold = max(REPEAT_FLOOR, math.ceil(REPEAT_FRACTION * n_pages))

    # count PAGES per signature, not lines: V-2024 paints its running header
    # twice on every page, which would double any line-based count.
    pages_by_sig: dict[str, set[int]] = defaultdict(set)
    for line in lines:
        if _in_band(line):
            pages_by_sig[_signature(line)].add(line.page)
    running = {sig for sig, pages in pages_by_sig.items() if len(pages) >= threshold}

    kept, dropped = [], []
    for line in lines:
        if _in_band(line) and (
            _signature(line) in running or _PAGE_NUMBER.fullmatch(line.text)
        ):
            dropped.append(line)
        else:
            kept.append(line)
    return kept, dropped


def median_leading(lines: list[Line]) -> float:
    """Typical baseline-to-baseline distance, measured within pages."""
    gaps = [
        b.y0 - a.y0
        for a, b in zip(lines, lines[1:])
        if a.page == b.page and 0 < b.y0 - a.y0 < 4 * max(a.size, b.size, 1.0)
    ]
    if gaps:
        return statistics.median(gaps)
    return 1.2 * statistics.median([line.size for line in lines]) if lines else 12.0


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text))


def _is_break(prev: Line, cur: Line, lead: float) -> bool:
    if cur.page != prev.page:
        return True
    if (prev.size, prev.bold) != (cur.size, cur.bold):
        return True
    if prev.leader or cur.leader:                 # a contents entry stands alone
        return True
    if _BULLET.match(cur.text):
        return True
    gap = cur.y0 - prev.y0
    if gap <= 0:                                  # moved up the page: new column or region
        return True
    if gap > GAP_SPLIT * lead:
        return True
    # PyMuPDF's layout block is only evidence. In L-2023 every line is its own
    # layout block, so honouring it unconditionally would shred every paragraph;
    # in KD-2025 a whole page is one block, so ignoring it would fuse the page.
    return cur.lb != prev.lb and (_ends_sentence(prev.text) or gap > LAYOUT_GAP_SPLIT * lead)


def split_blocks(lines: list[Line]) -> list[list[Line]]:
    """Group lines into blocks. Furniture must already be stripped."""
    if not lines:
        return []
    lead = median_leading(lines)
    groups: list[list[Line]] = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        if _is_break(prev, cur, lead):
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return groups


def join_lines(texts: list[str]) -> str:
    """One block's lines as one string, closing hyphenated word breaks.

    Three cases, and the first is the whole reason `clean_line` keeps the soft
    hyphen: a wrap marked with U+00AD, a wrap marked with a real hyphen, and a
    plain wrap that just needs a space.

    Soft hyphens that were not at a line end are dropped on the way out — 310 of
    them in kd-2015 alone. They mark where the typesetter *could* have broken a
    word and carry nothing once the block is one string, and leaving them in
    would put block text out of step with `corpus.normalize()`, which strips them
    before any citation is checked.
    """
    out = ""
    for raw in texts:
        piece = raw.strip()
        if not piece:
            continue
        if not out:
            out = piece
            continue
        if out.endswith("\xad") or out.endswith("¬"):
            out = out[:-1] + piece
        elif (
            out.endswith("-")
            and piece[:1].islower()
            and piece.split(" ", 1)[0].rstrip(",.") not in _SUSPENDED_NEXT
        ):
            out = out[:-1] + piece
        else:
            out = f"{out} {piece}"
    return out.replace("\xad", "")


def body_size(lines: list[Line]) -> float:
    """The size that carries the most text — the body face, whatever its rank."""
    volume: Counter[float] = Counter()
    for line in lines:
        volume[line.size] += len(line.text)
    return volume.most_common(1)[0][0] if volume else 0.0


def assign_roles(groups: list[list[Line]], lines: list[Line]) -> list[Block]:
    """Turn line groups into roled blocks.

    Provisional: roles come off size ratios, which is exactly the approach Task 3
    replaces with style clustering. Size ratios cannot see KD's back-cover topic
    labels (10.9pt against a 10.0pt body is a ratio of 1.09, under any usable
    heading cut) and they make the role of a block depend on how the splitter
    happened to group it.
    """
    body = body_size(lines) or 1.0
    heading_sizes = sorted({line.size for line in lines if line.size >= body * HEADING_RATIO}, reverse=True)

    blocks: list[Block] = []
    for i, group in enumerate(groups):
        text = join_lines([line.text for line in group])
        if not text:
            continue
        head = group[0]
        size = max(line.size for line in group)
        blocks.append(
            Block(
                id=f"b{i:04d}",
                role=_role_for(group, text, size, body, heading_sizes),
                page=head.page,
                size=size,
                bold=any(line.bold for line in group),
                text=text,
            )
        )
    return mark_toc(blocks)


def _role_for(
    group: list[Line], text: str, size: float, body: float, heading_sizes: list[float]
) -> str:
    if all(_broken_font_line(line.text) for line in group):
        # mp-2013's 50 section headings, set in a display font with no ToUnicode
        # map. Surfaced as an explicit placeholder rather than dropped.
        return "unreadable"
    if group[0].leader:
        return "toc"
    if _BULLET.match(text):
        return "bullet"
    if size >= body * HEADING_RATIO:
        rank = heading_sizes.index(size) if size in heading_sizes else len(heading_sizes)
        return f"h{min(rank + 1, 3)}"
    if len(text) <= 60 and group[0].bold and size >= body:
        return "label"
    return "para"


def mark_toc(blocks: list[Block]) -> list[Block]:
    """Re-role runs of contents entries.

    Provisional, and known to over-reach: a cover page is legitimately several
    large titles in a row. Task 3 restricts this to near-body-size runs.
    """
    run: list[int] = []
    for i, b in enumerate(blocks):
        if b.role != "toc" and _TOC_TAIL.search(b.text) and len(b.text) <= 120:
            run.append(i)
            continue
        if len(run) >= 3:
            for j in run:
                blocks[j].role = "toc"
        run = []
    if len(run) >= 3:
        for j in run:
            blocks[j].role = "toc"
    return blocks


def extract(pdf_bytes: bytes) -> Extraction:
    """Full pipeline over one PDF's bytes. Raises NoTextLayer for outline-only PDFs."""
    lines = read_lines(pdf_bytes)
    pages = (max(line.page for line in lines) + 1) if lines else 0
    kept, dropped = strip_running(lines)
    blocks = assign_roles(split_blocks(kept), kept)
    return Extraction(blocks=blocks, pages=pages, dropped=[line.text for line in dropped])


def blocks_from_text(text: str) -> list[Block]:
    """Degraded path: blocks from flat text, for a PDF with no text layer.

    `valmanifest-2022-c` is the only document in this state — 38 pages of vector
    outlines, 388 extractable characters. Its committed text comes from SND's
    plain-text rendition, where one line is already one paragraph (median 208
    chars, no hard wrapping), so a block per line is the honest reading. There is
    no font metric to recover, so nothing claims to be a heading.
    """
    body = re.sub(r"^<!--.*?-->\n", "", text, flags=re.S)
    blocks: list[Block] = []
    for line in body.splitlines():
        s = clean_line(line)
        if not s:
            continue
        blocks.append(
            Block(
                id=f"b{len(blocks):04d}",
                role="bullet" if _BULLET.match(s) else "para",
                page=0,
                size=0.0,
                bold=False,
                text=s.replace("\xad", ""),
            )
        )
    return blocks


_MD_PREFIX = {"h1": "# ", "h2": "## ", "h3": "### ", "bullet": "- ", "toc": "- "}


def to_markdown(blocks: list[Block]) -> str:
    """Debugging dump — deliberately not part of the corpus.

    The corpus has three consumers and each has a format: agents read the .txt,
    `verify simulate` checks the .txt, the site renders from blocks. A fourth
    representation would only be a thing to keep in sync.
    """
    out = []
    for b in blocks:
        text = b.text
        if b.role == "unreadable":
            out.append(f"<!-- unreadable heading, p{b.page + 1} -->")
        elif b.role == "label":
            out.append(f"**{text}**")
        else:
            out.append(f"{_MD_PREFIX.get(b.role, '')}{text}")
    return "\n\n".join(out) + "\n"
