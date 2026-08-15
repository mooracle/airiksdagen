"""The derived corpus: blocks on disk, and the .txt that must be a function of them.

Everything here runs against the committed artifacts rather than re-extracting
the PDFs. That is deliberate — what these tests are for is the guarantee that
`data/corpus/<slug>.txt` is exactly what `data/corpus/blocks/<slug>.json`
serializes, and re-deriving both from the source would test the extractor
against itself and prove nothing about what is committed.

The two interlocks are tested on their error paths, because that is the only
path that matters: a guard that has never refused anything is a guard nobody has
checked.
"""

import json
import re

import pytest

from aidag import corpus, docx
from aidag import extract_corpus as ec
from aidag.config import BLOCKS_DIR, CORPUS_DIR, FROZEN_DIR


def _blocks(slug: str) -> list[docx.Block]:
    path = BLOCKS_DIR / f"{slug}.json"
    if not path.exists():
        pytest.skip(f"{path} not built (run: uv run aidag extract-corpus)")
    return [docx.Block(**b) for b in json.loads(path.read_text())["blocks"]]


def _committed(slug: str) -> str:
    return (CORPUS_DIR / f"{slug}.txt").read_text(encoding="utf-8")


SLUGS = ec.cited_slugs()


class TestScope:
    def test_the_scope_is_the_23_cited_documents(self):
        """8 valmanifest + 15 partiprogram — what p6 lets an agent cite."""
        assert len(SLUGS) == 23
        assert len(ec.manifesto_slugs()) == 8
        assert len(ec.program_slugs()) == 15
        assert set(SLUGS) == set(ec.manifesto_slugs()) | set(ec.program_slugs())

    def test_the_out_of_scope_documents_are_untouched(self):
        """Budgetmotioner and Tidöavtalet have no source PDF this path can read.

        They keep their current extraction AND their current rendering, so their
        text must still be byte-identical to the frozen copy.
        """
        others = [
            p for p in sorted(CORPUS_DIR.glob("*.txt")) if p.stem not in set(SLUGS)
        ]
        assert len(others) == 17
        for path in others:
            assert path.read_bytes() == (FROZEN_DIR / path.name).read_bytes(), path.name

    @pytest.mark.parametrize("slug", SLUGS)
    def test_every_cited_document_has_blocks(self, slug):
        assert ec.blocks_path(slug).exists(), f"{slug}: no blocks — run extract-corpus"


class TestDerivedText:
    """The .txt is a function of the blocks, or it is not derived at all."""

    @pytest.mark.parametrize("slug", SLUGS)
    def test_the_committed_text_is_what_the_blocks_serialize(self, slug):
        blocks = _blocks(slug)
        header = _committed(slug).split("\n", 1)[0]
        assert ec.text_from_blocks(blocks, header) == _committed(slug)

    @pytest.mark.parametrize("slug", SLUGS)
    def test_it_round_trips_through_normalize_modulo_the_drop_set(self, slug):
        """`normalize()` on a derived document is a no-op on prose characters.

        Not a no-op: it still strips the provenance header, the digit-only lines
        and the undecodable display-font lines. That is the whole drop set, and
        what is left has to be the block text unchanged — otherwise the served
        text holds a string that exists in no block, which the anchor index
        could never resolve.
        """
        blocks = _blocks(slug)
        expected = "\n".join(b.text for b in blocks if not ec.normalize_drops(b.text))
        assert corpus.normalize(_committed(slug)) == expected

    @pytest.mark.parametrize("slug", SLUGS)
    def test_the_provenance_header_survives(self, slug):
        """export_site re-attaches it and doctext.parseProvenance renders it."""
        first = _committed(slug).split("\n", 1)[0]
        assert re.fullmatch(r"<!--\s.+\s-->", first), slug
        assert corpus.normalize(_committed(slug)).splitlines()[0] != first

    def test_the_drop_set_is_page_numbers_and_unreadable_headings_only(self):
        """Whatever normalize drops from a block must be one of those two things.

        Asserted on the REASON, not the role: a role allowlist wide enough to
        hold the roles a page number actually carries (`caption`, `toc`) says
        nothing about whether the block held prose, so a drop regex that widened
        far enough to swallow a paragraph would still pass it.
        """
        dropped = [
            b
            for slug in SLUGS
            for b in _blocks(slug)
            if ec.normalize_drops(b.text)
        ]
        assert dropped, "nothing is dropped — the round-trip proves nothing"
        for b in dropped:
            page_number = not re.search(r"[^\W\d_]", b.text, re.UNICODE)
            unreadable = docx._broken_font_line(b.text)  # noqa: SLF001
            assert page_number or unreadable, f"{b.role}: {b.text!r}"
            # an unreadable heading is kept as a block and roled as one, so the
            # page can surface it as a placeholder rather than lose it silently
            assert not unreadable or b.role == "unreadable", b.text

    def test_mp2013_unreadable_headings_are_kept_as_blocks(self):
        """Dropped from the served text, surfaced on the page as placeholders."""
        blocks = _blocks("partiprogram-mp-2013")
        unreadable = [b for b in blocks if b.role == "unreadable"]
        assert len(unreadable) >= 40
        assert all(ec.normalize_drops(b.text) for b in unreadable)

    def test_no_block_is_empty_and_ids_are_unique(self):
        for slug in SLUGS:
            blocks = _blocks(slug)
            assert all(b.text.strip() for b in blocks), slug
            assert len({b.id for b in blocks}) == len(blocks), slug


class TestEquivalenceGuard:
    """The 8 manifestos change source rendition — /txt to /pdf. Guard it."""

    @pytest.mark.parametrize("slug", ec.manifesto_slugs())
    def test_every_manifesto_lands_inside_the_band(self, slug):
        assert ec.check_equivalence(slug, _blocks(slug)) == pytest.approx(1.0, abs=0.05)

    def test_chart_furniture_is_not_counted_against_the_band(self):
        """valmanifest-m reads 1.06 on raw words and 0.995 on prose.

        Its 753 words of chart axis ticks, figure sources and rotated margin
        stamps are text the /txt rendition simply does not carry. Counting them
        would size the tolerance by one document's infographics rather than by
        whether SND is serving the same document twice.
        """
        blocks = _blocks("valmanifest-2022-m")
        frozen = len(ec._HEADER.sub("", ec.frozen_text("valmanifest-2022-m")).split())
        raw = sum(len(b.text.split()) for b in blocks)
        assert raw / frozen > 1 + ec.WORD_TOLERANCE
        assert ec.prose_words(blocks) / frozen == pytest.approx(0.995, abs=0.01)

    def test_a_rendition_missing_half_the_document_is_refused(self):
        blocks = _blocks("valmanifest-2022-kd")
        with pytest.raises(ValueError, match="not the same document"):
            ec.check_equivalence("valmanifest-2022-kd", blocks[: len(blocks) // 2])

    def test_an_empty_extraction_is_refused(self):
        with pytest.raises(ValueError, match="not the same document"):
            ec.check_equivalence("valmanifest-2022-kd", [])


class TestInterlocks:
    def test_a_document_with_no_frozen_copy_is_never_rewritten(self, tmp_path, monkeypatch):
        """The pre-p6 bytes are what full-v2 and full-v3 verify against.

        Rewriting a .txt before that copy exists would invalidate their
        citations with nothing left to compare against, so the check comes
        first and the file is left alone.
        """
        monkeypatch.setattr(ec, "FROZEN_DIR", tmp_path)
        before = (CORPUS_DIR / "valmanifest-2022-kd.txt").read_bytes()
        with pytest.raises(FileNotFoundError, match="no frozen copy"):
            ec.extract_slug("valmanifest-2022-kd")
        assert (CORPUS_DIR / "valmanifest-2022-kd.txt").read_bytes() == before

    def test_an_out_of_scope_slug_is_rejected(self):
        with pytest.raises(ValueError, match="not one of the 23"):
            ec.run(slug="tidoavtalet-2022")
        with pytest.raises(ValueError, match="not one of the 23"):
            ec.run(slug="budgetmotion-v-202223")

    def test_an_existing_extraction_is_not_redone_without_force(self, capsys):
        # guarded like _blocks(): without the committed block file this stops
        # being a skip test and becomes a real extraction that rewrites
        # data/corpus/valmanifest-2022-kd.txt in the working tree
        if not ec.blocks_path("valmanifest-2022-kd").exists():
            pytest.skip("blocks not built (run: uv run aidag extract-corpus)")
        assert ec.run(slug="valmanifest-2022-kd") == []
        assert "skipping" in capsys.readouterr().out


class TestProvenance:
    def test_a_programme_keeps_the_header_fetch_corpus_wrote(self):
        """Same document, same adoption date, same URL — only a better extractor."""
        from aidag.config import PARTY_PROGRAMS

        v = PARTY_PROGRAMS["KD"][0]
        header = ec.provenance("partiprogram-kd-2015", "pdf")
        assert v["title"] in header and v["from"] in header and v["url"] in header

    def test_a_manifesto_records_which_rendition_it_came_from(self):
        """The 8 manifestos are the documents whose SOURCE changed here."""
        pdf = ec.provenance("valmanifest-2022-kd", "pdf")
        assert "PDF-rendition" in pdf and pdf.endswith("/v/2022/pdf -->")
        text = ec.provenance("valmanifest-2022-c", "text")
        assert "textrendition" in text and text.endswith("/v/2022/txt -->")

    def test_the_outline_only_manifesto_says_so_on_disk(self):
        """valmanifest-2022-c has no text layer; its blocks come from SND's /txt."""
        meta = json.loads(ec.blocks_path("valmanifest-2022-c").read_text())
        assert meta["source"] == "text"
        assert "no usable text layer" in meta["note"]
        assert "textrendition" in _committed("valmanifest-2022-c").split("\n", 1)[0]
