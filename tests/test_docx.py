"""Structured extraction, pinned against the documents that break it.

Every fixture here is a real party document, because every failure this module
exists to fix is a real-document quirk: KD's soft-hyphenated wraps, KD-2025's
classification stamp in the header band, V-2024's chapter title that repeats its
own running header, L-2023's habit of emitting one layout block per line.

Role assignment is deliberately NOT pinned here beyond "the pipeline produces
one" — it is rewritten in Task 3 against style clusters, and asserting today's
size-ratio behaviour would only be something to delete.
"""

import pytest

from aidag import docx
from aidag.config import CORPUS_DIR
from aidag.fetch_corpus import cached_pdf, source_pdf_bytes


def _pdf(slug: str) -> bytes:
    if not cached_pdf(slug).exists():
        pytest.skip(f"{slug}: no cached PDF (run: uv run aidag fetch-corpus)")
    return source_pdf_bytes(slug)


def _line(
    text: str,
    *,
    page: int = 0,
    lb: int = 0,
    y0: float = 300.0,
    size: float = 11.0,
    bold: bool = False,
    leader: bool = False,
    x0: float = 70.0,
) -> docx.Line:
    return docx.Line(
        page=page, lb=lb, text=text, size=size, font="Test", bold=bold,
        x0=x0, y0=y0, x1=x0 + 300, y1=y0 + 12, page_w=595.0, page_h=842.0, leader=leader,
    )


@pytest.fixture(scope="module")
def kd_manifest() -> docx.Extraction:
    return docx.extract(_pdf("valmanifest-2022-kd"))


@pytest.fixture(scope="module")
def m_manifest() -> docx.Extraction:
    return docx.extract(_pdf("valmanifest-2022-m"))


@pytest.fixture(scope="module")
def kd_2025() -> docx.Extraction:
    return docx.extract(_pdf("partiprogram-kd-2025"))


@pytest.fixture(scope="module")
def l_2023() -> docx.Extraction:
    return docx.extract(_pdf("partiprogram-l-2023"))


class TestNoTextLayer:
    """valmanifest-2022-c is 38 pages of vector outlines and must say so."""

    def test_raised_for_the_outline_only_manifesto(self):
        with pytest.raises(docx.NoTextLayer) as exc:
            docx.read_lines(_pdf("valmanifest-2022-c"))
        assert exc.value.pages == 38
        assert exc.value.chars / exc.value.pages < docx.MIN_CHARS_PER_PAGE
        assert "no usable text layer" in str(exc.value)

    def test_not_raised_for_a_normal_pdf(self):
        lines = docx.read_lines(_pdf("valmanifest-2022-kd"))
        assert len(lines) > 100
        assert all(line.size > 0 and line.font for line in lines)
        assert all(line.text == line.text.strip() and line.text for line in lines)

    def test_the_floor_sits_in_a_gap_not_on_the_next_document(self):
        """946 chars/page is the corpus's second-lowest — the floor is not delicate."""
        lines = docx.read_lines(_pdf("valmanifest-2022-kd"))
        pages = max(line.page for line in lines) + 1
        per_page = sum(len(line.text) for line in lines) / pages
        assert per_page > 3 * docx.MIN_CHARS_PER_PAGE


class TestBlocksFromText:
    """The degraded path: C still gets blocks, and so a document page."""

    def test_the_outline_only_manifesto_still_yields_blocks(self):
        text = (CORPUS_DIR / "valmanifest-2022-c.txt").read_text()
        blocks = docx.blocks_from_text(text)
        assert len(blocks) > 100
        assert all(b.text.strip() for b in blocks)
        assert {b.role for b in blocks} <= {"para", "bullet"}
        assert [b.id for b in blocks] == sorted({b.id for b in blocks})
        joined = "\n".join(b.text for b in blocks)
        assert "Valet 2022 handlar om vilket Sverige vi vill ha" in joined

    def test_the_provenance_header_is_not_a_block(self):
        blocks = docx.blocks_from_text("<!-- Titel | antaget 2013 | http://x -->\nRiktig text\n")
        assert [b.text for b in blocks] == ["Riktig text"]

    def test_no_font_metric_is_invented(self):
        blocks = docx.blocks_from_text("EN RUBRIK I VERSALER\nEn mening som följer.\n")
        assert all(b.size == 0.0 and not b.bold for b in blocks)
        assert [b.role for b in blocks] == ["para", "para"]


class TestCleanLine:
    def test_keeps_the_soft_hyphen(self):
        """The one character that must survive: join_lines needs it."""
        assert docx.clean_line("drab\xad") == "drab\xad"

    def test_folds_ligatures_and_invisible_characters(self):
        assert docx.clean_line("uppﬁnning") == "uppfinning"
        assert docx.clean_line("a​b﻿ c\xa0d") == "ab c d"

    def test_drops_dot_leaders_and_collapses_space(self):
        assert docx.clean_line("Kapitel 1 ........ 14") == "Kapitel 1 14"
        assert docx.clean_line("  två    ord \n") == "två ord"

    def test_removes_a_not_sign_standing_inside_a_word(self):
        assert docx.clean_line("samman¬hang") == "sammanhang"


class TestJoinLines:
    def test_soft_hyphen_wrap_closes_without_a_space(self):
        assert docx.join_lines(["Kristde\xad", "mokraterna"]) == "Kristdemokraterna"

    def test_explicit_hyphen_wrap_closes_without_a_space(self):
        assert docx.join_lines(["fler utbild-", "ningsplatser"]) == "fler utbildningsplatser"

    def test_a_plain_wrap_gets_a_space(self):
        assert docx.join_lines(["en rad som", "fortsätter här"]) == "en rad som fortsätter här"

    def test_a_suspended_hyphen_is_not_a_wrap(self):
        """'el- och drivmedelspriser' must not become 'eloch drivmedelspriser'."""
        assert docx.join_lines(["el-", "och drivmedelspriser"]) == "el- och drivmedelspriser"

    def test_a_hyphen_before_a_capital_is_kept(self):
        assert docx.join_lines(["EU-", "Kommissionen"]) == "EU- Kommissionen"

    def test_residual_soft_hyphens_do_not_reach_the_block(self):
        assert docx.join_lines(["sam\xadmanhang och mer"]) == "sammanhang och mer"

    def test_empty_lines_are_ignored(self):
        assert docx.join_lines(["", "a", "  ", "b"]) == "a b"


class TestStripRunning:
    def test_drops_the_repeated_classification_stamp(self, kd_2025):
        """kd-2025 stamps 'Informationsklass: Intern' on all 69 pages."""
        assert not any("Informationsklass" in b.text for b in kd_2025.blocks)
        assert kd_2025.dropped.count("Informationsklass: Intern") == 69

    def test_keeps_a_repeated_prose_phrase_in_body_position(self):
        """'Moderaterna kommer att:' opens seven pages of the manifesto — mid-page."""
        lines = [_line("Moderaterna kommer att:", page=p, y0=400.0) for p in range(10)]
        kept, dropped = docx.strip_running(lines)
        assert len(kept) == 10 and not dropped

    def test_the_same_phrase_in_the_header_band_goes(self):
        lines = [_line("Vänsterpartiets valplattform 2022", page=p, y0=20.0) for p in range(10)]
        kept, dropped = docx.strip_running(lines)
        assert not kept and len(dropped) == 10

    def test_a_chapter_title_survives_its_own_running_header(self):
        """V-2024's 6pt header carries the chapter name; the 30pt title repeats it."""
        ex = docx.extract(_pdf("partiprogram-v-2024"))
        titles = [b for b in ex.blocks if b.text.strip() == "Frihetens hinder"]
        assert titles and titles[0].size == 30.0
        assert ex.dropped.count("Frihetens hinder") >= 3  # the 6pt header, dropped

    def test_a_footer_whose_number_changes_is_still_one_signature(self):
        """kd-2015 footers read 'PRINCIPPROGRAM | 5', '6 | PRINCIPPROGRAM', ..."""
        lines = [_line(f"PRINCIPPROGRAM | {p + 1}", page=p, y0=800.0) for p in range(8)]
        kept, dropped = docx.strip_running(lines)
        assert not kept and len(dropped) == 8

    def test_bare_page_numbers_in_the_band_go(self):
        lines = [_line(str(p + 1), page=p, y0=800.0) for p in range(6)]
        kept, dropped = docx.strip_running(lines)
        assert not kept and len(dropped) == 6

    def test_below_the_repeat_floor_nothing_is_dropped(self):
        """Two pages cannot establish a running header."""
        lines = [_line("Ett rubrikliknande stycke", page=p, y0=20.0) for p in range(2)]
        kept, dropped = docx.strip_running(lines)
        assert len(kept) == 2 and not dropped

    def test_no_prose_is_dropped_from_any_cited_document(self, kd_2025, m_manifest, l_2023):
        """The drop log is the invariant: furniture is short and repetitive."""
        for ex in (kd_2025, m_manifest, l_2023):
            for text in ex.dropped:
                assert len(text) <= 60, text


class TestSplitBlocks:
    def test_two_commitments_do_not_fuse(self, m_manifest):
        first = [b for b in m_manifest.blocks if b.text.startswith("• Bygga ut antalet praktikplatser")]
        second = [b for b in m_manifest.blocks if b.text.startswith("• Kräva aktivitet på heltid")]
        assert len(first) == 1 and len(second) == 1
        assert first[0].id != second[0].id
        assert "Kräva aktivitet" not in first[0].text

    def test_a_wrapped_paragraph_is_not_shredded(self, m_manifest):
        """Eleven typeset lines, one argument, one block."""
        hits = [b for b in m_manifest.blocks if b.text.startswith("Drivkrafter spelar roll")]
        assert len(hits) == 1
        assert hits[0].text.endswith("Att fler försörjer sig själva gynnar alla.")

    def test_per_line_layout_blocks_do_not_shred_l2023(self, l_2023):
        """PyMuPDF emits one layout block per line here; the splitter must ignore that."""
        hits = [b for b in l_2023.blocks if b.text.startswith("Vi är Sveriges liberala parti")]
        assert len(hits) == 1
        assert hits[0].text.endswith("frihet att forma sitt eget liv.")

    def test_a_page_wide_layout_block_still_splits_into_paragraphs(self, kd_2025):
        """KD-2025 puts a whole page in one layout block; leading has to do the work."""
        page = [b for b in kd_2025.blocks if b.page == 10]
        assert len(page) >= 6
        opener = [b for b in page if b.text.startswith("MÄNNISKAN EXISTERAR")]
        assert len(opener) == 1
        assert "Människans ofullkomlighet" not in opener[0].text

    def test_a_page_boundary_always_splits(self):
        lines = [_line("slutet av sidan", page=0, y0=800.0), _line("början av nästa", page=1, y0=80.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_a_style_change_splits(self):
        lines = [_line("En rubrik", y0=100.0, size=18.0, bold=True), _line("Brödtext.", y0=118.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_a_bullet_starts_a_block(self):
        lines = [_line("Vi vill:", y0=100.0), _line("• första punkten", y0=112.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_lines_in_one_paragraph_stay_together(self):
        lines = [_line(f"rad {i} utan slutpunktuering", y0=100.0 + 12 * i) for i in range(4)]
        assert len(docx.split_blocks(lines)) == 1

    def test_no_lines_no_blocks(self):
        assert docx.split_blocks([]) == []
        assert docx.median_leading([]) == 12.0


class TestExtraction:
    def test_block_ids_are_stable_and_unique(self, kd_manifest):
        ids = [b.id for b in kd_manifest.blocks]
        assert len(ids) == len(set(ids)) == len(kd_manifest.blocks)
        assert ids == sorted(ids)

    def test_every_block_carries_a_known_role(self, kd_manifest, l_2023):
        known = {"h1", "h2", "h3", "para", "bullet", "label", "toc", "caption", "unreadable"}
        for ex in (kd_manifest, l_2023):
            assert {b.role for b in ex.blocks} <= known

    def test_the_soft_hyphen_bug_is_gone(self, kd_manifest):
        """'Kristde mokraterna' is what stripping the hyphen before the join produces."""
        text = "\n".join(b.text for b in kd_manifest.blocks)
        assert "Kristde mokraterna" not in text
        assert "Kristdemokraterna" in text
        assert "\xad" not in text

    def test_unreadable_headings_are_surfaced_not_dropped(self):
        """mp-2013's display-font headings decode to gibberish; they stay as placeholders."""
        ex = docx.extract(_pdf("partiprogram-mp-2013"))
        assert sum(b.role == "unreadable" for b in ex.blocks) >= 40

    def test_to_markdown_is_a_dump_not_a_corpus_format(self, kd_manifest):
        md = docx.to_markdown(kd_manifest.blocks[:20])
        assert md.endswith("\n")
        assert "\xad" not in md
