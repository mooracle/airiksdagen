"""Download the party document corpus into data/corpus/ (committed to git).

- 2022 valmanifest for all 8 parties: SND Vivill plain-text (Public Domain Mark).
- Tidöavtalet: PDF from liberalerna.se, converted to text with pypdf.

Corpus files are the exact inputs shown to the agents; they are committed so
the research is reproducible without re-fetching.
"""

from __future__ import annotations

import httpx

from aidag.config import CORPUS_DIR, PARTY_CODES

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


def run(force: bool = False) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True, headers=HEADERS) as client:
        for code in PARTY_CODES:
            fetch_manifesto(client, code, force)
        fetch_tido(client, force)
    print("corpus complete")
