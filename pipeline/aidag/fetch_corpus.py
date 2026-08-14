"""Download the party document corpus into data/corpus/ (committed to git).

- 2022 valmanifest for all 8 parties: SND Vivill plain-text (Public Domain Mark).
- Tidöavtalet: PDF from liberalerna.se, converted to text with pypdf.
- Party programmes (partiprogram/principprogram/idéprogram), pinned to the
  version standing at the 2022 election — see config.PARTY_PROGRAMS for why the
  URLs are pinned rather than scraped from party sites.

Corpus files are the exact inputs shown to the agents; they are committed so
the research is reproducible without re-fetching.

Source PDFs are archived under data/corpus/pdf/ as they are fetched (see
config.PDF_DIR). Text extraction reads that cache when it is present, so the
corpus can be rebuilt without the network — which matters because the programme
URLs are live party-site links, not an archive.
"""

from __future__ import annotations

from html import unescape
from pathlib import Path

import httpx

from aidag.config import (
    BUDGET_MOTIONS,
    CORPUS_DIR,
    MANIFESTO_PDF_URL,
    PARTY_CODES,
    PARTY_PROGRAMS,
    PDF_DIR,
    SND_TXT_URL,
)

TIDO_PDF_URL = "https://www.liberalerna.se/wp-content/uploads/tidoavtalet-overenskommelse-for-sverige-slutlig.pdf"

HEADERS = {"User-Agent": "aidag-research/0.1 (open research project; contact via repo)"}


def cached_pdf(slug: str) -> Path:
    """Where the source PDF for a corpus slug lives ('partiprogram-kd-2015')."""
    return PDF_DIR / f"{slug}.pdf"


def source_pdf_bytes(slug: str) -> bytes:
    """Cached source bytes for `slug`, for extraction that must not hit the network.

    Raises FileNotFoundError rather than falling back to a fetch: a silent
    download here would mean an extraction run could quietly use a *different*
    edition of a party programme than the one the corpus was built from.
    """
    path = cached_pdf(slug)
    if not path.exists():
        raise FileNotFoundError(f"{slug}: no cached PDF at {path} — run `aidag fetch-corpus`")
    return path.read_bytes()


def _fetch_pdf(client: httpx.Client, slug: str, url: str, force: bool) -> bytes:
    """Source bytes for `slug`, from the cache unless it is missing or `force`.

    Filling this cache is the point: the 15 programme URLs are live party-site
    links and every one of those parties has replaced the pinned edition at least
    once, so an uncached document is one redesign away from unreproducible.
    """
    path = cached_pdf(slug)
    if path.exists() and not force:
        return path.read_bytes()
    r = client.get(url)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"{slug}: {url} did not return a PDF (starts {r.content[:16]!r})")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    print(f"  pdf/{path.name}: {len(r.content) // 1024} kB")
    return r.content


def fetch_manifesto(client: httpx.Client, code: str, force: bool) -> None:
    path = CORPUS_DIR / manifesto_filename(code)
    if path.exists() and not force:
        print(f"  {path.name}: exists, skipping")
        return
    url = SND_TXT_URL.format(code=code.lower())
    r = client.get(url)
    r.raise_for_status()
    text = r.text.strip()
    if len(text) < 2000:
        raise ValueError(f"{code}: suspiciously short manifesto ({len(text)} chars) from {url}")
    path.write_text(text + "\n")
    print(f"  {path.name}: {len(text)} chars")


def fetch_manifesto_pdf(client: httpx.Client, code: str, force: bool) -> None:
    """Archive SND's PDF rendition of a 2022 manifesto.

    Caching only — the .txt beside it keeps coming from SND's /txt endpoint. The
    manifestos are the 8 documents where structured extraction changes *source
    rendition*, not just extractor, and that swap is made deliberately (with a
    content-equivalence guard) where the corpus is regenerated, never as a side
    effect of filling this cache.
    """
    code = code.lower()
    _fetch_pdf(client, f"valmanifest-2022-{code}", MANIFESTO_PDF_URL.format(code=code), force)


def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract PDF text, preserving word integrity.

    Agents quote from the corpus text they are shown, and repair-citations
    requires those quotes to be exact substrings of it — a word mangled here
    becomes an unverifiable citation later.

    Uses PyMuPDF, NOT pypdf, and the difference is not cosmetic. These documents
    are typeset with discretionary hyphens inside words (U+00AD). PyMuPDF keeps
    them, so they strip back to the real word; pypdf converts them to SPACES
    ('be roende', 'kropps liga', 'samman hang'), destroying the word beyond
    recovery without a dictionary — 45+ words in KD's programme alone. pypdfium2
    was also tried and emits U+FFFE replacement characters.

    PyMuPDF is AGPL and is used only as a build-time converter; what the repo
    commits and ships is the extracted text.

    NOT applied to the existing p4 corpus (valmanifest, tidoavtalet): those bytes
    are the exact inputs full-v2's committed decisions came from and must not
    move mid-run.
    """
    import io
    import re
    import unicodedata

    import fitz  # PyMuPDF

    with fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)

    text = unicodedata.normalize("NFC", text)
    text = text.replace("­", "")                      # discretionary hyphens inside words
    text = text.replace("​", "")                      # zero-width spaces
    text = re.sub(r"(\w)-\s*\n\s*(?=[a-zåäö])", r"\1", text)   # hyphenation across a line break
    text = re.sub(r"\.{4,}\s*\d*", " ", text)              # table-of-contents dot leaders
    text = text.replace(" ", " ")                     # nbsp
    text = re.sub(r"[ \t]+", " ", text)
    # bare page numbers and rules: pure noise, and an agent can cite them
    text = "\n".join(
        s for line in text.splitlines()
        if (s := line.strip()) and not re.fullmatch(r"[\d\s.,%‑‒–—•·|-]+", s)
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_tido(client: httpx.Client, force: bool) -> None:
    """Tidöavtalet.

    NOTE: the .txt on disk is the FROZEN p4 extraction (pypdf) — full-v2's
    committed decisions were generated from those exact bytes and their citations
    verify against them, so it is never re-extracted. p5 serves this file through
    corpus.normalize(), which strips its page furniture at read time.
    """
    pdf_path = CORPUS_DIR / "tidoavtalet-2022.pdf"
    txt_path = CORPUS_DIR / "tidoavtalet-2022.txt"
    if txt_path.exists() and not force:
        print(f"  {txt_path.name}: exists, skipping")
        return
    if not pdf_path.exists() or force:
        r = client.get(TIDO_PDF_URL)
        r.raise_for_status()
        pdf_path.write_bytes(r.content)
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if len(text) < 20000:
        raise ValueError(f"Tidöavtalet extraction too short ({len(text)} chars)")
    txt_path.write_text(text + "\n")
    print(f"  {txt_path.name}: {len(reader.pages)} pages, {len(text)} chars")


def manifesto_filename(code: str) -> str:
    return f"valmanifest-2022-{code.lower()}.txt"


def program_filename(code: str, version: dict) -> str:
    return f"partiprogram-{code.lower()}-{version['from'][:4]}.txt"


def budget_filename(code: str, rm: str) -> str:
    return f"budgetmotion-{code.lower()}-{rm.replace('/', '')}.txt"


def fetch_programs(client: httpx.Client, force: bool) -> None:
    """Every version of every party's programme, extracted with PyMuPDF.

    The PDF is cached before the up-to-date check, not after it: an existing
    .txt used to short-circuit the whole document, which is exactly how these
    source bytes came to be discarded 15 times over.
    """
    for code, versions in PARTY_PROGRAMS.items():
        for v in versions:
            path = CORPUS_DIR / program_filename(code, v)
            pdf_bytes = _fetch_pdf(client, path.stem, v["url"], force)
            if path.exists() and not force:
                print(f"  {path.name}: exists, skipping")
                continue
            text = pdf_to_text(pdf_bytes)
            if len(text.split()) < 5000:
                raise ValueError(f"{path.name}: too short ({len(text.split())} words) — wrong document?")
            path.write_text(f"<!-- {v['title']} | antaget {v['from']} | {v['url']} -->\n{text}\n")
            print(f"  {path.name}: {len(text.split())} words (from {v['from']})")


def budget_narrative(html: str) -> str:
    """The motion's prose, without the appropriation tables.

    These run 13k-50k words. Measured on C 2022/23: only ~5.7k words sit inside
    <table> elements — the other ~44k are genuine policy prose, one section per
    utgiftsområde. That prose is the point (it is the party's stated position on
    each area being voted), so it is kept whole; only the numeric tables go.
    """
    import re
    import unicodedata

    text = re.sub(r"<table.*?</table>", "\n", html, flags=re.S | re.I)
    text = re.sub(r"<(script|style).*?</\1>", "\n", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)

    # Same word-integrity cleanup the PDF path gets. Riksdagen's HTML carries the
    # motion's typesetting, soft hyphens and all — C's 2022/23 budget alone holds
    # 322 of them. Left in, they sit inside words and turn any citation drawn
    # from them into an unverifiable quote.
    text = unicodedata.normalize("NFC", text)
    text = text.replace("­", "").replace("​", "").replace(" ", " ")
    text = re.sub(r"(\w)-\s*\n\s*(?=[a-zåäö])", r"\1", text)

    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"[\d\s.,%‑‒–—•·|-]+", s):   # page numbers, rules, table debris
            continue
        lines.append(s)
    out = re.sub(r"[ \t]+", " ", "\n".join(lines))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def fetch_budgets(client: httpx.Client, force: bool) -> None:
    """Shadow budgets for the four parties that file one (S, V, C, MP)."""
    for (code, rm), b in BUDGET_MOTIONS.items():
        path = CORPUS_DIR / budget_filename(code, rm)
        if path.exists() and not force:
            print(f"  {path.name}: exists, skipping")
            continue
        r = client.get(f"https://data.riksdagen.se/dokument/{b['dok_id']}.html")
        r.raise_for_status()
        text = budget_narrative(r.text)
        if len(text.split()) < 2000:
            raise ValueError(f"{path.name}: too short ({len(text.split())} words)")
        path.write_text(
            f"<!-- Budgetmotion {b['bet']} ({code}) | inlämnad {b['from']} | dok_id {b['dok_id']} -->\n{text}\n"
        )
        print(f"  {path.name}: {len(text.split())} words (from {b['from']})")


def run(force: bool = False) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180, follow_redirects=True, headers=HEADERS) as client:
        for code in PARTY_CODES:
            fetch_manifesto(client, code, force)
            fetch_manifesto_pdf(client, code, force)
        fetch_tido(client, force)
        fetch_programs(client, force)
        fetch_budgets(client, force)
    print("corpus complete")
