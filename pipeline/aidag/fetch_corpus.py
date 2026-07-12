"""Download the party document corpus into data/corpus/ (committed to git).

- 2022 valmanifest for all 8 parties: SND Vivill plain-text (Public Domain Mark).
- Tidöavtalet: PDF from liberalerna.se, converted to text with pypdf.
- Party programmes (partiprogram/principprogram/idéprogram), pinned to the
  version standing at the 2022 election — see config.PARTY_PROGRAMS for why the
  URLs are pinned rather than scraped from party sites.

Corpus files are the exact inputs shown to the agents; they are committed so
the research is reproducible without re-fetching.
"""

from __future__ import annotations

from html import unescape

import httpx

from aidag.config import BUDGET_MOTIONS, CORPUS_DIR, PARTY_CODES, PARTY_PROGRAMS

SND_TXT_URL = "https://snd.se/sv/vivill/file/{code}/v/2022/txt"
TIDO_PDF_URL = "https://www.liberalerna.se/wp-content/uploads/tidoavtalet-overenskommelse-for-sverige-slutlig.pdf"

HEADERS = {"User-Agent": "aidag-research/0.1 (open research project; contact via repo)"}


def fetch_manifesto(client: httpx.Client, code: str, force: bool) -> None:
    path = CORPUS_DIR / f"valmanifest-2022-{code.lower()}.txt"
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
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_tido(client: httpx.Client, force: bool) -> None:
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


def program_filename(code: str, version: dict) -> str:
    return f"partiprogram-{code.lower()}-{version['from'][:4]}.txt"


def budget_filename(code: str, rm: str) -> str:
    return f"budgetmotion-{code.lower()}-{rm.replace('/', '')}.txt"


def fetch_programs(client: httpx.Client, force: bool) -> None:
    """Every version of every party's programme, extracted with PyMuPDF."""
    for code, versions in PARTY_PROGRAMS.items():
        for v in versions:
            path = CORPUS_DIR / program_filename(code, v)
            if path.exists() and not force:
                print(f"  {path.name}: exists, skipping")
                continue
            r = client.get(v["url"])
            r.raise_for_status()
            text = pdf_to_text(r.content)
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

    text = re.sub(r"<table.*?</table>", "\n", html, flags=re.S | re.I)
    text = re.sub(r"<(script|style).*?</\1>", "\n", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = unescape(text)
    lines = [s for line in text.splitlines() if (s := line.strip())]
    out = "\n".join(lines)
    out = re.sub(r"[ \t]+", " ", out)
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
    with httpx.Client(timeout=180, follow_redirects=True, headers=HEADERS) as client:
        for code in PARTY_CODES:
            fetch_manifesto(client, code, force)
        fetch_tido(client, force)
        fetch_programs(client, force)
        fetch_budgets(client, force)
    print("corpus complete")
