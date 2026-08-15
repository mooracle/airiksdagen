"""Structured extraction, pinned against the documents that break it.

Every fixture here is a real party document, because every failure this module
exists to fix is a real-document quirk: KD's soft-hyphenated wraps, KD-2025's
classification stamp in the header band, V-2024's chapter title that repeats its
own running header, L-2023's habit of emitting one layout block per line.

Roles are pinned against the two documents that exposed the size-ratio approach:
KD's manifesto, whose back-cover topic labels sit 9% above a body that is itself
neither the largest nor the boldest face, and KD-2025, whose numbered
subheadings sit 9% above the body in the other direction. Both are `para` under
any ratio cut that does not also turn ordinary prose into headings.
"""

import json
import re

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
    width: float = 300.0,
    font: str = "Test",
    rotated: bool = False,
) -> docx.Line:
    return docx.Line(
        page=page, lb=lb, text=text, size=size, font=font, bold=bold,
        x0=x0, y0=y0, x1=x0 + width, y1=y0 + 12, page_w=595.0, page_h=842.0,
        leader=leader, rotated=rotated,
    )


def _reading(*extractions: docx.Extraction) -> str:
    """The documents as one string, block boundaries flattened to a space.

    A quote that crosses a block boundary still resolves — `verify simulate` and
    the anchor locator both match against normalized text, where a block break is
    whitespace. What must not survive is a *word* order the columns never had.
    """
    return re.sub(r"\s+", " ", "\n".join(b.text for ex in extractions for b in ex.blocks))


def _column_page() -> list[docx.Line]:
    """Two columns 20pt apart, in the interleaved order `sort=True` produces."""
    left = [_line(f"vänster {i}", y0=100.0 + 15 * i, x0=60.0, width=230.0) for i in range(4)]
    right = [_line(f"höger {i}", y0=100.0 + 15 * i, x0=310.0, width=230.0) for i in range(4)]
    return [line for pair in zip(left, right) for line in pair]


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


@pytest.fixture(scope="module")
def kd_2015() -> docx.Extraction:
    return docx.extract(_pdf("partiprogram-kd-2015"))


@pytest.fixture(scope="module")
def m_2021() -> docx.Extraction:
    return docx.extract(_pdf("partiprogram-m-2021"))


@pytest.fixture(scope="module")
def s_manifest() -> docx.Extraction:
    return docx.extract(_pdf("valmanifest-2022-s"))


@pytest.fixture(scope="module")
def mp_2013() -> docx.Extraction:
    return docx.extract(_pdf("partiprogram-mp-2013"))


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

    def test_a_roman_folio_in_the_band_goes(self):
        lines = [_line(n, page=p, y0=800.0) for p, n in enumerate(["i", "ii", "iii", "iv"])]
        kept, dropped = docx.strip_running(lines)
        assert not kept and len(dropped) == 4

    def test_a_title_made_only_of_roman_numeral_letters_survives(self):
        """The page-number rule is the one drop with no repetition requirement,
        so a single match deletes the line outright — and 'Vi vill' is nothing
        but i/v/l plus a space. Below the repeat floor so only that rule can fire."""
        for title in ("Vi vill", "Civil", "Mild"):
            kept, dropped = docx.strip_running([_line(title, page=0, y0=20.0)])
            assert [line.text for line in kept] == [title] and not dropped

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

    def test_a_page_boundary_splits_a_finished_sentence(self):
        lines = [_line("Slutet av sidan.", page=0, y0=800.0), _line("Början av nästa", page=1, y0=80.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_a_sentence_running_across_a_page_boundary_stays_whole(self):
        """312 of kd-2015's paragraphs end mid-clause at a page foot."""
        lines = [_line("meningen fortsätter", page=0, y0=800.0), _line("på nästa sida.", page=1, y0=80.0)]
        assert docx.join_lines([line.text for line in docx.split_blocks(lines)[0]]) == (
            "meningen fortsätter på nästa sida."
        )

    def test_a_hyphenated_word_across_a_page_boundary_closes(self):
        lines = [_line("de naturli-", page=0, y0=800.0), _line("ga gemenskaperna.", page=1, y0=80.0)]
        assert docx.join_lines([line.text for line in docx.split_blocks(lines)[0]]) == (
            "de naturliga gemenskaperna."
        )

    def test_a_new_paragraph_after_a_page_break_is_its_own_block(self):
        """A capital opener is the evidence that the previous thought ended."""
        lines = [_line("slutet av sidan", page=0, y0=800.0), _line("Början av nästa", page=1, y0=80.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_a_bullet_after_a_page_break_is_never_a_continuation(self):
        lines = [_line("slutet av sidan", page=0, y0=800.0), _line("• första punkten", page=1, y0=80.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_a_face_change_across_a_page_break_is_never_a_continuation(self):
        lines = [_line("slutet av sidan", page=0, y0=800.0), _line("större text", page=1, y0=80.0, size=18.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_pages_that_are_not_adjacent_never_join(self):
        lines = [_line("slutet av sidan", page=0, y0=800.0), _line("på nästa sida.", page=2, y0=80.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_cross_page_paragraphs_close_in_the_real_documents(self, kd_2015):
        text = _reading(kd_2015)
        assert "sträcker sig bortom de naturliga gemenskaperna" in text

    def test_a_hyphenated_word_across_any_other_split_closes_too(self):
        """`corpus.normalize()` closes this in the served text whatever caused it.

        A boundary it closes and the blocks do not is a word that exists in the
        text agents read and in no block — unresolvable by the anchor index. So
        the block level mirrors the rule exactly: 13 boundaries in the corpus,
        here a 20pt pull-quote whose leading exceeds the gap threshold.
        """
        lines = [
            _line("för sig själv, sina med-", y0=415.0, size=20.0),
            _line("människor, samhället", y0=439.0, size=20.0),
        ]
        groups = docx.split_blocks(lines)
        assert len(groups) == 1
        assert docx.join_lines([line.text for line in groups[0]]) == (
            "för sig själv, sina medmänniskor, samhället"
        )

    def test_a_hyphen_before_a_capital_still_splits(self):
        """Only a lower-case continuation is a wrap — normalize says the same."""
        lines = [_line("kortsiktiga mål-", y0=100.0), _line("Nästa stycke börjar", y0=200.0)]
        assert len(docx.split_blocks(lines)) == 2

    def test_the_split_word_is_whole_in_the_real_documents(self, m_2021):
        assert "sina medmänniskor" in _reading(m_2021)

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


class TestDetectColumns:
    """All 35 quotes the relink could not recover came from interleaved columns."""

    def test_a_sentence_crossing_the_gutter_survives(self, m_manifest):
        """p3's left column runs out mid-clause and resumes at the top of the right."""
        assert "Där det alltid lönar sig att arbeta och göra sitt bästa." in _reading(m_manifest)

    def test_two_columns_do_not_fuse_into_one_block(self, m_manifest):
        """p7 spliced a left-column bullet onto the right column's lead-in."""
        for b in m_manifest.blocks:
            assert not ("Kraftigt sänka kostnaden" in b.text and "Moderaterna kommer att:" in b.text)

    def test_the_manifesto_columns_read_down_not_across(self, m_manifest, s_manifest):
        text = _reading(m_manifest, s_manifest)
        assert "Alla nya löften och reformer – som till exempel våra" in text
        assert "Krafttag för att stoppa hedersrelaterat våld, sexualbrott och mäns våld mot kvinnor" in text

    def test_mp2013_columns_read_down_not_across(self, mp_2013):
        assert (
            "Det är en ödesfråga för landsbygden att samhället lyckas bryta "
            "beroendet av bensin och diesel." in _reading(mp_2013)
        )

    def test_a_synthetic_two_column_page_reads_down_then_across(self):
        ordered = docx.detect_columns(_column_page())
        assert [line.text for line in ordered] == [
            "vänster 0", "vänster 1", "vänster 2", "vänster 3",
            "höger 0", "höger 1", "höger 2", "höger 3",
        ]

    def test_a_single_column_page_is_returned_untouched(self):
        lines = [_line(f"rad {i}", y0=100.0 + 15 * i) for i in range(6)]
        assert docx.detect_columns(lines) == lines

    @pytest.mark.parametrize(
        "slug", ["partiprogram-l-2025", "partiprogram-s-2013", "valmanifest-2022-v"]
    )
    def test_single_column_documents_are_byte_for_byte_untouched(self, slug):
        kept, _ = docx.strip_running(docx.read_lines(_pdf(slug)))
        assert docx.detect_columns(kept) == kept

    def test_a_caption_beside_nothing_is_not_a_column(self):
        """Two blocks that never run alongside each other are one column, stacked."""
        top = [_line(f"överst {i}", y0=100.0 + 15 * i, x0=60.0, width=180.0) for i in range(2)]
        bottom = [_line(f"nederst {i}", y0=400.0 + 15 * i, x0=330.0, width=180.0) for i in range(2)]
        assert docx.detect_columns(top + bottom) == top + bottom

    def test_a_lone_marginal_line_is_not_a_column(self):
        """One line either side of a wide gap is a margin note, not a column."""
        body = [_line(f"rad {i}", y0=100.0 + 15 * i, x0=60.0, width=230.0) for i in range(6)]
        stamp = [_line("Valmanifest 2022", y0=140.0, x0=520.0, width=12.0)]
        assert docx.detect_columns(body[:3] + stamp + body[3:]) == body[:3] + stamp + body[3:]

    def test_rotated_lines_leave_the_flow(self):
        """A rotated stamp bridges the chart band and the columns beneath it."""
        body = [_line(f"rad {i}", y0=100.0 + 15 * i, x0=60.0, width=230.0) for i in range(3)]
        stamp = _line("Valmanifest 2022", y0=120.0, x0=560.0, width=11.0, rotated=True)
        assert docx.detect_columns([body[0], stamp, body[1], body[2]]) == body + [stamp]

    def test_the_rotated_margin_stamp_never_lands_in_prose(self, m_manifest):
        """39 of them, once per page, sitting mid-page where the band rule cannot see."""
        stamps = [b for b in m_manifest.blocks if b.text == "Valmanifest 2022"]
        assert len(stamps) >= 30
        assert {b.role for b in stamps} == {"caption"}


class TestStyleClusters:
    def test_the_body_is_the_face_carrying_most_text(self, kd_manifest):
        """KD sets its body in 10pt Regular — neither the largest nor the boldest."""
        body = kd_manifest.clusters[0]
        assert (body.size, body.bold) == (10.0, False)
        assert any(c.size > body.size and c.bold for c in kd_manifest.clusters[1:])

    def test_a_larger_bolder_face_is_still_prose_when_its_blocks_are_long(self, kd_manifest):
        """43% of KD's manifesto is 11pt SemiBold prose over a 10pt Regular body."""
        prose = [c for c in kd_manifest.clusters if c.size == 11.0 and c.bold and c.share > 0.1]
        assert len(prose) == 1
        assert prose[0].median_chars >= docx.PROSE_BLOCK_CHARS
        assert prose[0].role == "para"

    def test_clusters_are_ranked_by_volume_and_share_sums_to_one(self, l_2023):
        chars = [c.chars for c in l_2023.clusters]
        assert chars == sorted(chars, reverse=True)
        assert sum(c.share for c in l_2023.clusters) == pytest.approx(1.0)

    def test_tracking_artefacts_fold_into_one_face(self, m_manifest):
        """valmanifest-m reports its single 11pt body as 11.0/11.1/11.2/11.3."""
        for a in m_manifest.clusters:
            for b in m_manifest.clusters:
                if a is not b and a.font == b.font:
                    assert abs(a.size - b.size) > docx.SIZE_TOLERANCE * max(a.size, b.size)

    def test_the_marginal_glossary_is_a_label_face(self, kd_2015):
        """237 one-word terms set 1.5pt below the body, in the margin."""
        labels = [c for c in kd_2015.clusters if c.role == "label" and c.blocks > 200]
        assert len(labels) == 1
        assert labels[0].size < kd_2015.clusters[0].size and labels[0].bold

    def test_no_groups_no_clusters(self):
        assert docx.style_clusters([]) == []
        assert docx.assign_roles([]) == ([], [])


class TestRoles:
    def test_kd_back_cover_labels_share_one_role_and_it_is_not_para(self, kd_manifest):
        """10.9pt ExtraBold against a 10.0pt body: ratio 1.09, under any heading cut.

        Across three splitter iterations these classified para -> toc -> mixed,
        because the rules keyed off block length and run position. They are one
        list at one face and must come out as one role.
        """
        labels = [
            b for b in kd_manifest.blocks
            if b.size == 10.9 and any(
                w in b.text for w in ("FLER JOBB", "STARKARE FAMILJER", "BÄTTRE OMSORG",
                                      "STÄRKT CIVILSAMHÄLLE", "JÄMSTÄLLDHET PÅ RIKTIGT")
            )
        ]
        assert len(labels) >= 5
        assert len({b.role for b in labels}) == 1
        assert labels[0].role != "para"

    def test_kd2025_numbered_subheadings_are_headings(self, kd_2025):
        """12.0pt against an 11.0pt body — the same 1.09 ratio, the other way up."""
        subheads = [b for b in kd_2025.blocks if b.size == 12.0 and b.text.startswith("2.")]
        assert len(subheads) >= 4
        assert {b.role for b in subheads} == {"h2"}

    def test_a_numbered_heading_is_not_re_read_as_a_bullet(self, mp_2013):
        heads = [b for b in mp_2013.blocks if b.text.strip() == "1. Grön ideologi"]
        assert heads and heads[0].role.startswith("h")

    def test_page_numbers_set_larger_than_any_heading_are_captions(self, m_2021):
        """m-2021 sets its page numbers at 28pt, above every real heading."""
        numbers = [b for b in m_2021.blocks if b.text.isdigit()]
        assert len(numbers) >= 10
        assert {b.role for b in numbers} == {"caption"}

    def test_the_section_heading_face_is_h2_not_h3(self):
        """Levels pivot on the most-used heading face, so a rail can be built.

        Ranked by size, sd-2023's 34 section headings would sit below a 52pt
        cover wordmark and a 26pt date and come out h3, leaving a chapter rail
        built from h1/h2 with two entries, both of them cover art.
        """
        ex = docx.extract(_pdf("partiprogram-sd-2023"))
        heads = [b for b in ex.blocks if b.role == "h2"]
        assert len(heads) >= 20
        assert any(b.text.strip() == "Inledning" for b in heads)

    def test_every_role_is_decided_once_per_face(self, l_2023):
        """Two blocks at the same face and neither bulleted nor a contents entry
        cannot disagree about what they are."""
        seen: dict[tuple[float, bool], set[str]] = {}
        for b in l_2023.blocks:
            if b.role in ("bullet", "toc", "unreadable", "caption"):
                continue
            seen.setdefault((b.size, b.bold), set()).add(b.role)
        assert all(len(roles) == 1 for roles in seen.values())


class TestMarkToc:
    def test_contents_entries_are_marked(self, kd_2015):
        entries = [b for b in kd_2015.blocks if b.role == "toc"]
        assert len(entries) >= 20
        assert any(b.text.startswith("2.1. Demokrati") for b in entries)

    def test_a_near_body_run_is_marked(self):
        blocks = [
            docx.Block(id=f"b{i}", role="para", page=0, size=10.0, bold=False, text=t)
            for i, t in enumerate(["Inledning 5", "Demokrati 12", "Ekonomi 20", "Brödtext."])
        ]
        assert [b.role for b in docx.mark_toc(blocks, 10.0)] == ["toc", "toc", "toc", "para"]

    def test_a_cover_run_of_large_titles_is_left_alone(self):
        """A cover page is legitimately four big titles in a row, some ending in a
        number — which is all `_TOC_TAIL` can see."""
        blocks = [
            docx.Block(id=f"b{i}", role="h1", page=0, size=40.0, bold=True, text=t)
            for i, t in enumerate(["Valmanifest 2022", "Sverige 2030", "Vår plan 4", "Redo 2"])
        ]
        assert {b.role for b in docx.mark_toc(blocks, 10.0)} == {"h1"}


class TestKnownUnrecovered:
    """The residual allowlist, kept as a ratchet rather than a running total.

    The whole point is that the Task 7 build gate can tell known-and-accepted
    from newly-broken, which it cannot do if the list is allowed to grow
    quietly. Measuring it needs the full full-v4 record and takes about a minute,
    so the corpus-wide check belongs in `migrate-quotes`; what is pinned here is
    that the file stays well-formed and never gets longer.
    """

    def test_the_allowlist_is_well_formed_and_only_shrinks(self):
        payload = json.loads((CORPUS_DIR / "known-unrecovered.json").read_text())
        quotes = payload["quotes"]
        assert payload["run_id"] == "full-v4"
        assert len(quotes) <= 12
        assert sum(q["citations"] for q in quotes) <= 119
        for q in quotes:
            assert q["quote"].strip() and q["citations"] >= 1
            assert (CORPUS_DIR / f"{q['document']}.txt").exists()

    def test_every_listed_quote_really_is_unrecoverable(self):
        """If one of these starts resolving it belongs out of the file, not in it."""
        payload = json.loads((CORPUS_DIR / "known-unrecovered.json").read_text())
        for slug in {q["document"] for q in payload["quotes"]}:
            text = _reading(docx.extract(_pdf(slug)))
            for q in payload["quotes"]:
                if q["document"] == slug:
                    assert re.sub(r"\s+", " ", q["quote"]).strip() not in text


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
