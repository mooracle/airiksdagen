"""Structured extraction: a party PDF into blocks with stable ids.

`fetch_corpus.pdf_to_text()` flattens a document to lines and throws away the
three things that say what a line *is* — font size, weight and where it sits on
the page. The site then tries to guess them back from line lengths, which is why
`partiprogram-m-2021` renders 187 false subheadings and `valmanifest-2022-v`
renders none at all for 51k characters.

This module keeps the metrics instead. The order is load-bearing:

    read_lines -> strip_running -> detect_columns -> split_blocks -> assign_roles

Furniture goes first: a page number stripped after blocking has already landed
inside a block, and blocks are what citations anchor to. Columns come next,
because a block cannot be split out of a line sequence that already interleaves
two columns. Roles go last, because a role is a statement about a block relative
to the rest of the document (see `style_clusters`) and cannot be decided line by
line.

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
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from itertools import groupby

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

# ...and the same territory turned on its side. `valmanifest-2022-m` stamps
# "Valmanifest 2022" up the outer edge of all 39 of its inner pages, rotated, in
# a strip from x=18.9 to x=30.2 of a 595pt page. Vertically it is centred —
# y 0.448 to 0.552 — so the top/bottom band never sees it, and 39 copies of the
# document's own title were reaching the page as body text.
#
# Only rotated lines are tested against this, and that is the whole safety of it:
# upright text at x<10% is the left edge of every line of a single-column page.
# Rotation plus the outer margin plus the same repetition threshold is a running
# stamp; any one of the three alone is prose, a chart or an ornament.
MARGIN = 0.10

# A drop cap: one letter set this many times the body size, with the paragraph
# wrapped around it. m-2021 sets its 18 at 186.5pt against a 10pt body (18.7x),
# so the ratio does not need to be delicate — what it must not reach is an
# ordinary heading, and the largest of those in this corpus is 49.9pt (5.0x).
DROP_CAP_RATIO = 8.0
# How often the fragment must stand as a word elsewhere before a space is put
# back between it and the cap. Its own occurrence is discounted first.
DROP_CAP_WORD_FLOOR = 2

# Block splitting, in multiples of the document's median leading.
GAP_SPLIT = 1.65        # a vertical jump this big is a new block anywhere
LAYOUT_GAP_SPLIT = 1.20  # ...or this big when PyMuPDF also reports a new layout block

# Reading order. A gutter is a vertical strip of the page that no line crosses.
# valmanifest-m sets its two columns 11.7pt apart on p24 and 12.0pt apart on p7,
# so anything near 2% of a 595pt page splits the document against itself. The
# floor can afford to be low: within one column at least one line reaches the
# measure, so a column has no internal channel to find. What keeps a drop cap or
# a marginal note from reading as a column is MIN_COLUMN_LINES and the
# requirement that the two sides overlap vertically.
COLUMN_GUTTER = 0.012
MIN_GUTTER_PT = 6.0
MIN_COLUMN_LINES = 2
MAX_CUT_DEPTH = 24
# A horizontal region boundary is a blank line, in multiples of the median line
# height. Ordinary leading must not qualify: cut at every inter-line gap and a
# two-column page becomes one band per *row*, each with a single line either
# side of the gutter, and the columns interleave exactly as before.
BAND_GAP = 0.8

# Style clustering. Two faces are the same face when their sizes differ by less
# than this: PDF text can be horizontally scaled or tracked, which is why
# valmanifest-m reports its one 11pt body as 11.0/11.1/11.2/11.3.
SIZE_TOLERANCE = 0.03

# A cluster whose typical block runs this long is prose whatever its size or
# weight. KD's manifesto sets 43% of its text in 11pt SemiBold against a 10pt
# Regular body — larger *and* bolder than the body, and still prose.
PROSE_BLOCK_CHARS = 120

# ...and a cluster below body size carrying this little of the document, in
# short blocks, is furniture the band rule cannot see: chart labels, photo
# credits. It used to catch valmanifest-m's rotated 'Valmanifest 2022' margin
# stamp too — as `caption`, which put 39 small copies of the document's own
# title through the body text. That one is furniture rather than a caption and
# is now dropped outright by `_in_margin`; what is left here is the genuinely
# minor text that belongs on the page.
MINOR_SHARE = 0.05
CAPTION_BLOCK_CHARS = 90

# mark_toc only looks at near-body runs. A cover page is legitimately four large
# titles in a row, and several of them end in a number.
TOC_MAX_RATIO = 1.35

# `mid_clause_cuts`: how many unterminated paragraphs in a row stop being damage
# and become a list. Two is a paragraph the extraction cut twice; three is a
# glossary, a contents list or a page of headline pledges.
LIST_RUN = 3
# The roles a cut paragraph can have. `label`, `toc` and the headings are the
# roles that legitimately end without punctuation, and `caption`/`unreadable`
# are not prose at all.
PROSE_ROLES = ("para",)

_DIGITS = re.compile(r"\d+")
_BULLET = re.compile(r"^(?:[•·▪◦‣⁃]|[–—-]\s|\(?\d{1,2}[.)]\s|[a-zA-ZåäöÅÄÖ][.)]\s)")
_LEADER = re.compile(r"\.{4,}")
_PAGE_NUMBER = re.compile(r"[\d\s.,%‑‒–—•·|ivxlcdmIVXLCDM-]+")
# A well-formed roman numeral, checked against the line's letters rather than
# left to the character class above. The class alone has no repetition
# requirement, so one match drops the line — and 'civil', 'vill' and 'Vi vill'
# are nothing but i/v/x/l/c/d/m, which would delete a chapter title that happens
# to sit in the band (see `strip_running`; V-2024's 30pt titles sit at 5.5%).
_ROMAN = re.compile(r"m*(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", re.I)
# Swedish words that *are* well-formed roman numerals, so the numeral check above
# passes them. 'Vi' (= VI) is the one that matters: the multi-word case is safe
# because 'Vi vill' is not a numeral, but a column break can leave 'Vi' alone on a
# line, and dropping it deletes the opening word of a pledge. Nothing is lost by
# refusing them — 'vi' is folio 6 and 'di' folio 501, and no cited document
# numbers its pages in roman at all. Bare 'i' is deliberately absent: it is a
# preposition that never stands as its own line, and it is the first folio of any
# roman-numbered front matter (`test_a_roman_folio_in_the_band_goes`).
_ROMAN_WORDS = {"vi", "di"}
# Same idea without the roman numerals: at block level they would swallow real
# words ('civil' is nothing but i/v/c/l).
_NUMERIC_ONLY = re.compile(r"[\d\s.,%‑‒–—•·|-]+")
_SENTENCE_END = re.compile(r"[.!?:;][\"'”’)\]]?$")
_TOC_TAIL = re.compile(r"\S\s+\d{1,3}$")
# The two halves of corpus.normalize()'s hyphenation rule, applied at block
# boundaries by `_word_continues`.
_HYPHEN_END = re.compile(r"\w-$")
_LOWER_START = re.compile(r"[a-zåäö]")

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
    rotated: bool = False


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
class Cluster:
    """One typographic face, and how much of the document it carries.

    A role is a property of the face, not of the individual block: KD's five
    back-cover topic labels are one list at one style, and any rule keyed off
    block length or run position classified them three different ways across
    three splitter iterations. Cluster the faces first, decide once per face.

    The face is `(size, font)` and not `(size, weight)`, because weight is too
    coarse to separate KD's 10.9pt Barlow-ExtraBold labels from the 11.0pt
    Barlow-SemiBold prose beside them: same weight, sizes 0.9% apart, and the
    size tolerance folds them into one cluster. The font name does not.
    """

    size: float
    font: str
    bold: bool
    chars: int
    blocks: int
    median_chars: float
    share: float
    role: str = "para"

    @property
    def key(self) -> tuple[float, str]:
        return (self.size, self.font)


@dataclass(slots=True)
class Extraction:
    blocks: list[Block]
    pages: int
    dropped: list[str] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)


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
                            rotated=_is_rotated(ln.get("dir")),
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


def _is_rotated(direction) -> bool:
    """True for text not running left-to-right — margin stamps and chart axes.

    Rotated text is never part of the reading flow, and its bounding box is a
    tall thin sliver that bridges otherwise separate regions of the page. Left in
    place it welds valmanifest-m's chart band to the two prose columns below it,
    hiding the gutter between them (see `detect_columns`).
    """
    if not direction:
        return False
    dx, dy = direction[0], direction[1]
    return abs(dy) > 0.01 or dx < 0.99


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


def _in_margin(line: Line) -> bool:
    """A rotated line running up either outer edge — furniture territory sideways.

    Rotation is required, not incidental: `_in_band`'s horizontal equivalent
    would otherwise match the start of every ordinary line on the page. The
    rotated lines this corpus actually has are a running stamp (`valmanifest-2022-m`,
    x 0.032-0.051), chart axis labels (the same document's 2012-2018 tick labels,
    x 0.18-0.61, which are mid-page and must survive), a pull-quote ornament
    (`partiprogram-v-2024`, x 0.54-0.61) and two one-off headings. Only the first
    is inside the margin, and only it repeats.
    """
    if not line.rotated:
        return False
    return line.x1 < MARGIN * line.page_w or line.x0 > (1 - MARGIN) * line.page_w


def _is_page_number(text: str) -> bool:
    """A folio: arabic, bare punctuation, or a well-formed roman numeral.

    The letters are checked as a numeral rather than as a character class,
    because this is the one drop rule with no repetition requirement — a single
    match deletes the line from the blocks AND from the derived .txt, and the
    only trace is the drop log. 'Vi vill' opens a page in more than one of these
    manifestos.
    """
    if not _PAGE_NUMBER.fullmatch(text):
        return False
    letters = "".join(c for c in text if c.isalpha())
    if letters.lower() in _ROMAN_WORDS:
        return False
    return not letters or bool(_ROMAN.fullmatch(letters))


def strip_running(lines: list[Line]) -> tuple[list[Line], list[Line]]:
    """Split off running headers, footers and page numbers. Returns (kept, dropped).

    Two conditions, both required, because either alone loses prose:

      position   only the top/bottom tenth of the page is furniture territory —
                 or, for a ROTATED line, the left/right tenth (see `_in_margin`)
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
        if _in_band(line) or _in_margin(line):
            pages_by_sig[_signature(line)].add(line.page)
    running = {sig for sig, pages in pages_by_sig.items() if len(pages) >= threshold}

    kept, dropped = [], []
    for line in lines:
        if (_in_band(line) or _in_margin(line)) and (
            _signature(line) in running or _is_page_number(line.text)
        ):
            dropped.append(line)
        else:
            kept.append(line)
    return kept, dropped


def detect_columns(lines: list[Line]) -> list[Line]:
    """Reading order for multi-column pages; single-column pages come back as-is.

    PyMuPDF's `sort=True` orders layout blocks roughly by (y, x), which reads a
    two-column page across the gutter instead of down it. Every one of the 35
    quotes the relink could not recover is that failure: 'Kollektivtrafik … krävs
    Många på land' is the end of a left-column sentence with the top of the right
    column spliced into it.

    The fix is a recursive XY-cut. A vertical cut is taken only through genuine
    whitespace with at least MIN_COLUMN_LINES either side and the two sides
    overlapping vertically — that is what a column is, and what a caption or a
    drop cap is not. Failing that the page is split into horizontal bands and
    each band re-examined, which is what lets a full-width headline sit above two
    columns without hiding the gutter beneath it.

    Rotated lines are lifted out first and re-appended at the end of the page in
    left-to-right order: they are margin stamps and chart axis labels, not flow.

    A page whose horizontal lines take no vertical cut is returned untouched
    rather than re-sorted, so single-column documents cannot be perturbed here.
    """
    out: list[Line] = []
    for _, group in groupby(lines, key=lambda line: line.page):
        page = list(group)
        flow = [line for line in page if not line.rotated]
        aside = [line for line in page if line.rotated]
        ordered, cut = _xy_cut(flow, 0)
        out += (ordered if cut else flow) + sorted(aside, key=lambda line: (line.x0, line.y0))
    return out


def _xy_cut(lines: list[Line], depth: int) -> tuple[list[Line], bool]:
    """(ordered lines, whether a column split was taken anywhere below)."""
    if len(lines) < 2 or depth >= MAX_CUT_DEPTH:
        return lines, False
    if (cut := _gutter(lines)) is not None:
        left = [line for line in lines if line.x1 <= cut]
        right = [line for line in lines if line.x1 > cut]
        return _xy_cut(left, depth + 1)[0] + _xy_cut(right, depth + 1)[0], True
    if (y := _widest_band_gap(lines)) is not None:
        top = [line for line in lines if line.y1 <= y]
        rest = [line for line in lines if line.y1 > y]
        to, tc = _xy_cut(top, depth + 1)
        ro, rc = _xy_cut(rest, depth + 1)
        return to + ro, tc or rc
    # Nothing to prove here, so change nothing. Re-sorting by (y, x) would undo
    # PyMuPDF's own layout analysis, which reads a two-column page correctly
    # whenever it puts each column in its own layout block — most of the time.
    return lines, False


def _gutter(lines: list[Line]) -> float | None:
    """x of the widest usable column gutter, or None if the lines are one column."""
    spans = sorted((line.x0, line.x1) for line in lines)
    floor = max(MIN_GUTTER_PT, COLUMN_GUTTER * lines[0].page_w)
    gaps: list[tuple[float, float, float]] = []
    end = spans[0][1]
    for x0, x1 in spans[1:]:
        if x0 - end >= floor:
            gaps.append((x0 - end, end, x0))
        end = max(end, x1)
    for _, a, b in sorted(gaps, reverse=True):
        cut = (a + b) / 2
        left = [line for line in lines if line.x1 <= cut]
        right = [line for line in lines if line.x1 > cut]
        if len(left) >= MIN_COLUMN_LINES and len(right) >= MIN_COLUMN_LINES:
            if _overlaps_vertically(left, right):
                return cut
    return None


def _overlaps_vertically(left: list[Line], right: list[Line]) -> bool:
    """Columns run alongside each other. A caption below a figure does not."""
    top = max(min(line.y0 for line in left), min(line.y0 for line in right))
    bottom = min(max(line.y1 for line in left), max(line.y1 for line in right))
    return bottom > top


def _widest_band_gap(lines: list[Line]) -> float | None:
    """y of the widest full-width blank line, or None if there is none.

    One cut per step, and the gutter is re-tested on each side afterwards. That
    ordering is the whole difference between reading valmanifest-m p3 as two
    columns and reading it as three stacked pairs of half-columns: its two
    columns share paragraph gaps at the same heights, and splitting at every gap
    at once turns each shared gap into a false region boundary.
    """
    floor = BAND_GAP * statistics.median([line.y1 - line.y0 for line in lines])
    best: tuple[float, float] | None = None
    end = -math.inf
    for line in sorted(lines, key=lambda line: line.y0):
        gap = line.y0 - end
        if end > -math.inf and gap > floor and (best is None or gap > best[0]):
            best = (gap, end)
        end = max(end, line.y1)
    return best[1] if best else None


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


def _runs_on(prev: list[Line], group: list[Line]) -> bool:
    """True when a paragraph broken by a page boundary is one paragraph.

    `_is_break` splits on every page change, which is right for the page itself
    and wrong for the sentence running across it. Unclosed, that split is most of
    what the fragmentation figure measures: 312 of kd-2015's 764 paragraphs and
    220 of kd-2025's 655 end mid-clause at a page foot.

    Deliberately conservative. A new paragraph after a page break starts with a
    capital in every one of these documents, so a lower-case opener (or a
    hyphenated word) is the evidence; a bullet, a contents entry, a change of
    face or a closed sentence all veto.
    """
    a, b = prev[-1], group[0]
    if b.page != a.page + 1 or a.rotated or b.rotated:
        return False
    if (a.size, a.bold, a.font) != (b.size, b.bold, b.font):
        return False
    if a.leader or b.leader or _BULLET.match(b.text) or _ends_sentence(a.text):
        return False
    if a.text.endswith(("\xad", "-", "¬")):
        return True
    return b.text[:1].islower()


def _word_continues(prev: list[Line], group: list[Line]) -> bool:
    """True when the split falls inside a hyphenated word.

    Mirrors `corpus.normalize()`'s cross-line hyphenation join exactly, and must
    keep mirroring it. The corpus .txt is derived from these blocks and served
    through `normalize()`, so a boundary normalize closes and this does not is a
    word that exists in the served text and in no block — unquotable by the
    anchor index, and invisible until a citation lands on it.

    Thirteen boundaries in the corpus, all real wraps the geometry could not see:
    m-2021's 20pt pull-quote whose leading exceeds the split threshold, and
    kd-2015/kd-2025/mp-2013 paragraphs continuing into the next column.

    Deliberately no suspended-hyphen exemption ('el- och drivmedelspriser'),
    which `join_lines` does apply within a block: normalize has none either, and
    agreeing with it matters more here than being right about the hyphen. None
    occur today, and the round-trip test fails loudly if one ever does.
    """
    return bool(
        _HYPHEN_END.search(prev[-1].text) and _LOWER_START.match(group[0].text)
    )


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

    joined: list[list[Line]] = [groups[0]]
    for group in groups[1:]:
        if _runs_on(joined[-1], group) or _word_continues(joined[-1], group):
            joined[-1] = joined[-1] + group
        else:
            joined.append(group)
    return joined


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


def _block_face(group: list[Line]) -> tuple[float, str]:
    """The face a block is set in: its size, and the font carrying most of it.

    Size is constant within a block — `_is_break` splits on any size change — but
    a font can vary inside one, so the dominant font wins for the same reason
    `read_lines` types a line by its longest span.
    """
    volume: dict[str, int] = defaultdict(int)
    for line in group:
        volume[line.font] += len(line.text)
    return (max(line.size for line in group), max(volume, key=volume.__getitem__))


def style_clusters(groups: list[list[Line]]) -> list[Cluster]:
    """The document's typographic faces, ranked by how much text each carries.

    The body face is simply the first one: the face that sets the most characters
    is the body, whatever its size or weight. That is what makes this work where
    size ratios do not — KD's manifesto sets its body in 10pt Regular and 43% of
    its prose in a *larger, bolder* 11pt SemiBold, and the second is prose all the
    same. A ratio against the body cannot say so; the typical block length can.

    Sizes within SIZE_TOLERANCE of an already-accepted face are folded into it,
    largest volume first, so tracking artefacts do not fragment the body.
    """
    raw: dict[tuple[float, str], list[int]] = defaultdict(list)
    # Weight, don't overwrite. `_is_bold` ORs PyMuPDF's flag with a font-name
    # test, so boldness is not uniform within a face — two of `partiprogram-kd-
    # 2015`'s are mixed. Taking the last block's value made the face's weight a
    # function of document order, and `_role_per_cluster` reads it twice: a face
    # is `label` when it is bold and the BODY is not, so one stray bold block in
    # the body face silences the role for the whole document. The dominant
    # weight by character volume is the same choice `_block_face` makes about
    # the font, and reproduces the committed roles for all 23 documents.
    weight: dict[tuple[float, str], list[int]] = defaultdict(lambda: [0, 0])
    for group in groups:
        text = join_lines([line.text for line in group])
        if text:
            face = _block_face(group)
            raw[face].append(len(text))
            weight[face][0] += len(text) if any(line.bold for line in group) else 0
            weight[face][1] += len(text)
    bold = {face: n * 2 > total for face, (n, total) in weight.items()}

    merged: dict[tuple[float, str], list[int]] = {}
    for face in sorted(raw, key=lambda k: (-sum(raw[k]), -k[0])):
        host = next(
            (
                h
                for h in merged
                if h[1] == face[1] and abs(h[0] - face[0]) <= SIZE_TOLERANCE * max(h[0], face[0])
            ),
            face,
        )
        merged.setdefault(host, []).extend(raw[face])

    total = sum(sum(lengths) for lengths in merged.values()) or 1
    clusters = [
        Cluster(
            size=size,
            font=font,
            bold=bold[(size, font)],
            chars=sum(lengths),
            blocks=len(lengths),
            median_chars=statistics.median(lengths),
            share=sum(lengths) / total,
        )
        for (size, font), lengths in merged.items()
    ]
    clusters.sort(key=lambda c: (-c.chars, -c.size))
    return _role_per_cluster(clusters)


def _role_per_cluster(clusters: list[Cluster]) -> list[Cluster]:
    """Decide one role per face. Order of the tests is the argument.

    Prose first, because a face can be bigger and bolder than the body and still
    be prose — length is the only honest evidence. Then size against the body,
    because anything larger is a heading of some level. Then weight at or below
    body size, which is the `label`: KD-2015's 237 marginal glossary terms,
    valmanifest-l's numbered commitment leads. Anything left that is small,
    short and rare is `caption` — chart axis labels, photo credits, and the
    rotated margin stamps the band rule in `strip_running` cannot reach.
    """
    if not clusters:
        return []
    body = clusters[0]
    headings: list[Cluster] = []
    for c in clusters[1:]:
        if c.median_chars >= PROSE_BLOCK_CHARS:
            c.role = "para"
        elif c.size > body.size:
            headings.append(c)
        elif c.bold and not body.bold:
            c.role = "label"
        elif c.share <= MINOR_SHARE and c.median_chars <= CAPTION_BLOCK_CHARS:
            c.role = "caption"
        else:
            c.role = "para"

    # Levels pivot on the heading face used most often — that is the section
    # heading, the one a chapter rail is built from. Ranking purely by size puts
    # a cover-art wordmark at h1 and buries the sections at h3.
    if headings:
        pivot = max(headings, key=lambda c: (c.blocks, c.chars))
        for c in headings:
            c.role = "h2" if c is pivot else ("h1" if c.size > pivot.size else "h3")
    return clusters


def assign_roles(groups: list[list[Line]]) -> tuple[list[Block], list[Cluster]]:
    """Turn line groups into roled blocks, plus the clusters the roles came from."""
    clusters = style_clusters(groups)
    by_face = {c.key: c for c in clusters}

    def role_of(face: tuple[float, str]) -> str:
        if c := by_face.get(face):
            return c.role
        # a folded size: find the face it was folded into
        near = [
            c
            for c in clusters
            if c.font == face[1] and abs(c.size - face[0]) <= SIZE_TOLERANCE * max(c.size, face[0])
        ]
        return max(near, key=lambda c: c.chars).role if near else "para"

    body = clusters[0].size if clusters else 0.0
    blocks: list[Block] = []
    for i, group in enumerate(groups):
        text = join_lines([line.text for line in group])
        if not text:
            continue
        face = _block_face(group)
        blocks.append(
            Block(
                id=f"b{i:04d}",
                role=_role_for(group, text, role_of(face)),
                page=group[0].page,
                size=face[0],
                bold=any(line.bold for line in group),
                text=text,
            )
        )
    return mark_toc(blocks, body), clusters


def _role_for(group: list[Line], text: str, cluster_role: str) -> str:
    if all(_broken_font_line(line.text) for line in group):
        # mp-2013's 50 section headings, set in a display font with no ToUnicode
        # map. Surfaced as an explicit placeholder rather than dropped.
        return "unreadable"
    if all(line.rotated for line in group):
        # valmanifest-m's 39 'Valmanifest 2022' margin stamps and its rotated
        # chart axis labels — outside the flow, whatever face they are set in.
        return "caption"
    if group[0].leader:
        return "toc"
    if _NUMERIC_ONLY.fullmatch(text):
        # m-2021 sets its 22 page numbers at 28pt, larger than any heading in the
        # document. They are not chapters.
        return "caption"
    if cluster_role != "para":
        # the face has already been decided; a heading that opens '1. ' is still
        # a heading and must not be re-read as a bullet.
        return cluster_role
    return "bullet" if _BULLET.match(text) else "para"


def mark_toc(blocks: list[Block], body: float) -> list[Block]:
    """Re-role runs of contents entries — near body size only.

    Restricted to `body * TOC_MAX_RATIO` because a cover page is legitimately
    four large titles in a row and several of them end in a number, which is all
    `_TOC_TAIL` can see. Unrestricted it re-roled 14 of KD's 45 manifesto blocks.
    """
    limit = body * TOC_MAX_RATIO
    run: list[int] = []
    for i, b in enumerate(blocks):
        if (
            b.role != "toc"
            and b.size <= limit
            and _TOC_TAIL.search(b.text)
            and len(b.text) <= 120
        ):
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


def merge_drop_caps(blocks: list[Block]) -> list[Block]:
    """Put an initial back on the paragraph it was lifted out of.

    `partiprogram-m-2021` opens each of its 18 chapters with a drop cap — one
    letter at 186.5pt against a 10pt body, with the paragraph set around it. The
    extraction sees a giant one-character block and roles it `h1`, so the document
    rendered 18 chapter headings reading "M", "P", "V", "E"… and the paragraph
    beside each one began mid-word: "oderata Samlingspartiet verkar i en
    liberalkonservativ tradition…". Both halves of that are the same bug.

    The cap does not sit next to its paragraph in reading order — the XY-cut puts
    it at the end of the page — so it is paired by page: the first `para` on the
    cap's page that begins with a lowercase letter is the one it was taken from.
    A paragraph does not otherwise start lowercase, which is what makes the pair
    unambiguous.

    WHETHER TO PUT A SPACE BACK is decided by the document's own vocabulary, not
    guessed. Usually the cap is the first letter of the word ("M" + "oderata"),
    but sometimes it is a word in its own right ("I" + "ett fritt samhälle"), and
    Swedish gives no way to tell from the letter alone. Two signals together:
    the joined form is unattested elsewhere in the document AND the fragment
    stands as a word on its own elsewhere. Either alone gets a case wrong —
    "vår"/"när" are attested joined and would take a space on the fragment test
    ("V" + "år", "N" + "är"), and "kunskapsinnehållet" appears exactly once so
    the joined test alone splits it into "K unskapsinnehållet".
    """
    if not blocks:
        return blocks
    body = statistics.median([b.size for b in blocks if b.role == "para"] or [0])
    if not body:
        return blocks
    vocab: defaultdict[str, int] = defaultdict(int)
    for b in blocks:
        for w in re.findall(r"[^\W\d_]+", b.text, re.UNICODE):
            vocab[w.lower()] += 1

    caps = [
        b for b in blocks
        if len(b.text.strip()) == 1 and b.text.strip().isalpha() and b.size >= DROP_CAP_RATIO * body
    ]
    if not caps:
        return blocks
    merged: dict[int, str] = {}
    drop: set[int] = set()
    for cap in caps:
        letter = cap.text.strip()
        target = next(
            (
                b for b in blocks
                if b.page == cap.page and b.role == "para" and b.text[:1].islower()
                and id(b) not in merged
            ),
            None,
        )
        if target is None:
            continue
        m = re.match(r"[^\W\d_]+", target.text, re.UNICODE)
        if not m:
            continue
        frag = m.group(0)
        joined_attested = vocab[(letter + frag).lower()] > 0
        # its own occurrence discounted: a fragment appearing only here is not
        # evidence that it stands as a word
        frag_attested = vocab[frag.lower()] - 1 >= DROP_CAP_WORD_FLOOR
        sep = " " if (not joined_attested and frag_attested) else ""
        merged[id(target)] = letter + sep + target.text
        drop.add(id(cap))

    out = []
    for b in blocks:
        if id(b) in drop:
            continue
        if id(b) in merged:
            b = Block(id=b.id, role=b.role, page=b.page, size=b.size, bold=b.bold, text=merged[id(b)])
        out.append(b)
    # ids are positional and the ones above have just moved
    return [
        Block(id=f"b{i:04d}", role=b.role, page=b.page, size=b.size, bold=b.bold, text=b.text)
        for i, b in enumerate(out)
    ]


def extract(pdf_bytes: bytes) -> Extraction:
    """Full pipeline over one PDF's bytes. Raises NoTextLayer for outline-only PDFs."""
    lines = read_lines(pdf_bytes)
    pages = (max(line.page for line in lines) + 1) if lines else 0
    kept, dropped = strip_running(lines)
    blocks, clusters = assign_roles(split_blocks(detect_columns(kept)))
    blocks = merge_drop_caps(blocks)
    return Extraction(
        blocks=blocks,
        pages=pages,
        dropped=[line.text for line in dropped],
        clusters=clusters,
    )


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


def mid_clause_cuts(blocks: list[Block]) -> list[int]:
    """Indices of prose blocks the extraction cut in the middle of a clause.

    The plan's acceptance metric is "paragraph fragmentation < 20% per document",
    and the naive reading of it — a `para` block with no terminal punctuation —
    measures the wrong thing. It reads 32.7% on kd-2015 and 30.7% on kd-2025, and
    almost all of that is their glossaries: 210 of kd-2015's 216 hits are the 8pt
    marginal term list, 189 of kd-2025's 190 are the `Ordlista` appendix. A
    dictionary entry has no full stop because that is the form of a dictionary
    entry. The same false positive covers sd-2019's and mp-2013's contents lists
    and valmanifest-m's page of headline pledges.

    What separates them is not the block, it is the neighbourhood. A list is a
    RUN — three or more unterminated paragraphs in a row at one size. A cut
    cannot chain that way: the extraction splits a paragraph at a column or page
    boundary and the tail it leaves behind finishes the sentence, so a genuine
    cut is bounded by a terminated block. Runs are therefore excluded, and what
    survives is mostly the real defect (valmanifest-m p4, 'Sverige' / 'har redan
    ett av världens högsta skattetryck' — a two-column page read straight
    across).

    Mostly, not entirely: a glossary whose entries sometimes do end in a full
    stop breaks into short runs, and 17 of kd-2025's 18 remaining hits are
    Ordlista entries rather than cuts. The rule takes that document from 30.7% to
    2.9% and kd-2015 from 32.7% to 2.9%, which is enough for a 20% gate to mean
    something; it is not a classifier, and a use that needs one should not read
    this number.

    Measured on the corpus: 145 cuts over 6,036 prose blocks (2.4%), worst
    document valmanifest-m at 11.2%. Blocks, not rendered paragraphs: the site's
    `groupBlocks()` rejoins what it can, but it also fuses kd-2025's Ordlista
    into 5 run-on paragraphs, so measuring there would score the extraction on a
    rendering artefact.
    """
    prose = [i for i, b in enumerate(blocks) if b.role in PROSE_ROLES]
    open_ended = [not _SENTENCE_END.search(blocks[i].text) for i in prose]
    cuts: list[int] = []
    i = 0
    while i < len(prose):
        if not open_ended[i]:
            i += 1
            continue
        j = i
        while (
            j + 1 < len(prose)
            and open_ended[j + 1]
            and blocks[prose[j + 1]].size == blocks[prose[i]].size
        ):
            j += 1
        if j - i + 1 < LIST_RUN:
            cuts.extend(prose[i : j + 1])
        i = j + 1
    return cuts


def fragmentation(blocks: list[Block]) -> tuple[int, int]:
    """(cuts, prose blocks) for one document — the acceptance metric's numerator
    and denominator, so a caller reporting a share cannot pick a different one."""
    return len(mid_clause_cuts(blocks)), sum(1 for b in blocks if b.role in PROSE_ROLES)


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
