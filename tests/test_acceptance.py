"""The plan's acceptance criteria, as assertions instead of a one-off check.

Every item here was measured by hand once while the corpus rebuild was being
built. Measuring by hand is how a criterion quietly stops being true: the
extraction, the migration and the repair pass all rewrite committed data, and
the next person to re-run one of them has no way to know which guarantees they
just spent. So each criterion is pinned against the committed record, with the
numbers it was accepted at.

These read `data/` and `site/src/data/`, which are committed but large, and skip
rather than fail when a stage has not been run — the convention the rest of the
suite already follows.

Two criteria are asserted elsewhere and are named here so the list is complete:

  paragraphs render      `site/tests/corpus.test.mjs` — a statement about the
                         page, and the page is TypeScript. This file checks that
                         all 40 documents are exported and how they split.

  block <-> served line   `anchors.block_index`, which raises rather than
                         returns. `tests/test_anchors.py` breaks it on purpose.
"""

import json
import re

import pytest

from aidag import docx
from aidag.config import BLOCKS_DIR, PROCESSED_DIR, RESULTS_DIR, SITE_DATA_DIR

RUN = "full-v4"
FROZEN_RUN = "full-v3"

# The plan's gate. Documented as "paragraph fragmentation < 20% per document";
# `docx.mid_clause_cuts` carries the restatement and why it was needed.
FRAGMENTATION_GATE = 0.20

# Measured on full-v4 and accepted in Task 10. A ratchet, not a description: a
# re-run that moves either number has changed what the site publishes.
N_CITATIONS = 77_719
N_BLANK = 120

ABOUT_PAGE = SITE_DATA_DIR.parent / "pageviews" / "AboutPage.astro"


def _skip_unless(path, how: str):
    if not path.exists():
        pytest.skip(f"{path} absent (run: {how})")
    return path


@pytest.fixture(scope="module")
def blocks() -> dict[str, dict]:
    """Every re-extracted document, as `blocks/<slug>.json` holds it."""
    _skip_unless(BLOCKS_DIR, "uv run aidag extract-corpus")
    out = {}
    for path in sorted(BLOCKS_DIR.glob("*.json")):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    if not out:
        pytest.skip("no extracted documents")
    return out


@pytest.fixture(scope="module")
def citations() -> list[dict]:
    """(document class, quote, parti, datum, excused) for every citation in the run.

    Flat rows rather than decisions: every criterion below counts citations, and
    the two that do not — the flags — are counted off the same walk.
    """
    import polars as pl

    sim = _skip_unless(RESULTS_DIR / "simulations" / RUN, f"uv run aidag agent-ingest --run-id {RUN}")
    _skip_unless(PROCESSED_DIR / "cases.parquet", "uv run aidag build-cases")
    datum = {
        r["votering_id"]: r["datum"]
        for r in pl.read_parquet(
            PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum"]
        ).iter_rows(named=True)
    }
    rows = []
    for path in sorted(sim.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            flags = d.get("flags") or []
            for c in d.get("citations", []):
                rows.append(
                    {
                        "document": c["document"],
                        "quote": (c.get("quote") or "").strip(),
                        "before": c.get("quote_fore_migrering"),
                        "parti": d["parti"],
                        "datum": datum.get(d["votering_id"], ""),
                        "votering_id": d["votering_id"],
                        "excused": "citat_ej_migrerat" in flags,
                    }
                )
    return rows


class TestEveryCitationIsAccountedFor:
    """Criterion 1: resolve to a block, be blank, or be flagged `citat_ej_migrerat`.

    The third disposition exists so a known-unrecoverable quote does not fail the
    build. It is currently unused — `repair-citations` blanks those 119 instead —
    and that is worth asserting rather than assuming, because "0 excused" and
    "119 excused" are very different claims about the corpus.
    """

    def test_every_citation_resolves_or_is_blank(self, citations):
        from aidag import anchors
        from aidag.migrate_quotes import resolve_slug

        tally = {"located": 0, "blank": 0, "excused": 0, "out_of_scope": 0}
        unlocated = []
        for c in citations:
            if not c["quote"]:
                tally["blank"] += 1
                continue
            slug = resolve_slug(c["document"], c["parti"], c["datum"])
            if slug is None:
                # budgetmotioner and Tidöavtalet keep the flat extraction; p6
                # shows neither, so this stays at 0
                tally["out_of_scope"] += 1
                continue
            if anchors.block_index(slug).locate(c["quote"]) is not None:
                tally["located"] += 1
            elif c["excused"]:
                tally["excused"] += 1
            else:
                unlocated.append((slug, c["votering_id"], c["quote"][:70]))
        assert not unlocated, f"{len(unlocated)} citations locate nowhere, e.g. {unlocated[:3]}"
        assert sum(tally.values()) == N_CITATIONS
        assert tally == {"located": 77_599, "blank": N_BLANK, "excused": 0, "out_of_scope": 0}

    def test_a_blank_citation_keeps_the_agents_words(self, citations):
        """Blanking withdraws a claim about the corpus; it does not erase evidence."""
        blank = [c for c in citations if not c["quote"]]
        assert len(blank) == N_BLANK
        sim = RESULTS_DIR / "simulations" / RUN
        kept = sum(
            1
            for path in sorted(sim.glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for c in json.loads(line).get("citations", [])
            if not (c.get("quote") or "").strip() and c.get("quote_ej_verifierad")
        )
        assert kept == N_BLANK

    def test_the_migrated_quotes_changed_no_word(self, citations):
        """Task 6 kept the pre-migration English on this measurement — so pin it.

        Every rewrite `migrate-quotes` made is spacing, hyphenation or a
        hyphen/en-dash swap. If one ever changes a word, the committed English
        translation of that citation is stale and the decision to keep it lapses.
        """
        letters = re.compile(r"[^0-9a-zà-öø-ÿ]")
        migrated = [c for c in citations if c["before"] is not None]
        assert len(migrated) == 520
        for c in migrated:
            assert letters.sub("", c["before"].lower()) == letters.sub("", c["quote"].lower()), (
                f"{c['votering_id']}: migration changed a word, not just spacing — "
                f"{c['before'][:60]!r} -> {c['quote'][:60]!r}"
            )


class TestNoWithdrawnQuoteSurvivesInEnglish:
    """`repair-citations` blanks a Swedish quote it cannot verify; the English
    was translated before that pass and is still committed, so every export that
    pairs the two positionally has to withhold it.

    There are TWO such exports, and only one of them is the case JSON: the party
    page loads `aggregates/coalition.json` whole, and its `override_cases[].en`
    is built from the same translation rows. Both are asserted here because the
    failure is silent — the lists stay the same length, and the withdrawn text
    ships as machine-readable data whether or not any page renders it.
    """

    def _pairs(self, sv: list[dict], en: dict | None):
        en_cits = (en or {}).get("citations") or []
        return [
            (i, en_cits[i]["quote"])
            for i, c in enumerate(sv)
            if i < len(en_cits)
            and not (c.get("quote") or "").strip()
            and (en_cits[i].get("quote") or "").strip()
        ]

    def test_the_exported_cases_withhold_it(self):
        cases = _skip_unless(SITE_DATA_DIR / "cases", f"uv run aidag export-site --run-id {RUN}")
        leaked, blank = [], 0
        for path in sorted(cases.glob("*.json")):
            case = json.loads(path.read_text(encoding="utf-8"))
            for parti, d in (case.get("ai") or {}).items():
                sv = d.get("citations") or []
                blank += sum(1 for c in sv if not (c.get("quote") or "").strip())
                for i, q in self._pairs(sv, d.get("en")):
                    leaked.append((path.stem, parti, i, q[:60]))
        assert not leaked, f"{len(leaked)} withdrawn quotes still in English, e.g. {leaked[:3]}"
        assert blank == N_BLANK

    def test_the_coalition_override_cards_withhold_it(self):
        path = _skip_unless(
            SITE_DATA_DIR / "aggregates" / "coalition.json",
            f"uv run aidag build-aggregates --run-id {RUN}",
        )
        cards = json.loads(path.read_text(encoding="utf-8")).get("override_cases") or []
        assert cards, "coalition.json carries no override cases — the check would be vacuous"
        leaked = [
            (c["parti"], c["votering_id"], i, q[:60])
            for c in cards
            for i, q in self._pairs(c.get("citations") or [], c.get("en"))
        ]
        assert not leaked, f"{len(leaked)} withdrawn quotes still in English, e.g. {leaked[:3]}"


class TestVerifyIsGreenForBothRuns:
    """Criterion 2. full-v3 is p5 and reads `data/corpus/frozen/`; full-v4 is p6
    and reads the re-extracted files. Both have to pass, and they pass against
    different bytes — which is the whole point of the frozen split."""

    @pytest.mark.parametrize("run_id", [FROZEN_RUN, RUN])
    def test_no_citation_is_hallucinated(self, run_id):
        from aidag.simulate import verify_run

        _skip_unless(RESULTS_DIR / "simulations" / run_id, "uv run aidag agent-ingest")
        _skip_unless(PROCESSED_DIR / "cases.parquet", "uv run aidag build-cases")
        failed = [(name, detail) for name, ok, detail in verify_run(run_id) if not ok]
        assert not failed, f"{run_id}: {failed}"


class TestNoFurnitureSurvivesIntoProse:
    """Criterion 3, against the drop log each extraction writes beside its blocks.

    Stated at the granularity that means something. Most of the 1,056 dropped
    lines are bare page numbers, and asking whether "3" occurs inside a block
    answers a question about the digit three. The claim worth holding is that no
    *identifying* furniture line — a running header or footer — was spliced into
    a paragraph.
    """

    LETTER = re.compile(r"[^\W\d_]")
    PROSE = {"para", "bullet"}

    def _identifying(self, line: str) -> bool:
        return len(line) >= 4 and len(self.LETTER.findall(line)) >= 3

    def test_no_running_header_is_spliced_into_a_paragraph(self, blocks):
        checked = 0
        leaks = []
        for slug, doc in blocks.items():
            furniture = sorted({l for l in doc["dropped"] if self._identifying(l)})
            checked += len(furniture)
            prose = [b for b in doc["blocks"] if b["role"] in self.PROSE]
            for line in furniture:
                leaks += [(slug, b["id"], line) for b in prose if line in b["text"]]
        assert checked >= 150, f"only {checked} identifying furniture lines — drop log looks empty"
        assert not leaks, f"furniture inside prose blocks: {leaks[:5]}"

    def test_a_repeated_chapter_name_is_kept_where_it_is_a_title(self, blocks):
        """The other half of the rule, and the reason the drop signature carries
        the face. V-2024 paints the chapter name as a 6pt running header on every
        page of the chapter; the chapter's own 30pt title says the same words.
        The header goes, the title stays."""
        doc = blocks.get("partiprogram-v-2024")
        if doc is None:
            pytest.skip("partiprogram-v-2024 not extracted")
        assert "Frihetens hinder" in doc["dropped"]
        titles = [b for b in doc["blocks"] if b["text"] == "Frihetens hinder"]
        assert [b["role"] for b in titles] == ["h1"]
        assert titles[0]["size"] > 20


class TestParagraphFragmentation:
    """Criterion 4, on the restated metric — see `docx.mid_clause_cuts`."""

    def test_every_document_is_under_the_gate(self, blocks):
        over = {}
        for slug, doc in blocks.items():
            cuts, prose = docx.fragmentation([docx.Block(**b) for b in doc["blocks"]])
            share = cuts / prose if prose else 0.0
            if share >= FRAGMENTATION_GATE:
                over[slug] = f"{cuts}/{prose} = {share:.1%}"
        assert not over, f"over the {FRAGMENTATION_GATE:.0%} gate: {over}"

    def test_the_documents_the_plan_named_are_the_ones_that_moved(self, blocks):
        """kd-2025 and valmanifest-m were the two the plan called out at ~40%."""
        for slug, ceiling in (("partiprogram-kd-2025", 0.05), ("valmanifest-2022-m", 0.15)):
            doc = blocks.get(slug)
            if doc is None:
                pytest.skip(f"{slug} not extracted")
            cuts, prose = docx.fragmentation([docx.Block(**b) for b in doc["blocks"]])
            assert cuts / prose < ceiling, f"{slug}: {cuts}/{prose}"

    def test_a_glossary_is_not_what_the_number_measures(self, blocks):
        """The false positive the restatement exists for.

        Read naively, kd-2025 fails the gate at 30.7% — and 189 of those 190
        blocks are its `Ordlista` appendix, dictionary entries that have no full
        stop because dictionary entries do not. The run rule takes it to 2.9%.
        The residual is not zero (17 of the 18 are still glossary entries, in
        short runs the rule cannot see), which is why the docstring says the
        number is a gate and not a classifier.
        """
        doc = blocks.get("partiprogram-kd-2025")
        if doc is None:
            pytest.skip("partiprogram-kd-2025 not extracted")
        bl = [docx.Block(**b) for b in doc["blocks"]]
        glossary = next((i for i, b in enumerate(bl) if b.text.startswith("ORDLISTA")), None)
        assert glossary is not None, "the Ordlista appendix moved — re-check this fixture"
        prose = [i for i, b in enumerate(bl) if b.role in docx.PROSE_ROLES]
        naive = [i for i in prose if not docx._SENTENCE_END.search(bl[i].text)]
        assert len(naive) / len(prose) > FRAGMENTATION_GATE, "naive metric no longer fails"
        assert sum(1 for i in naive if i >= glossary) / len(naive) > 0.9
        assert len(docx.mid_clause_cuts(bl)) / len(prose) < 0.05

    def test_a_cut_paragraph_is_still_counted(self):
        """And the true positive, from valmanifest-m p4: a two-column page read
        straight across, leaving 'Sverige' / 'har redan ett av världens...'."""
        bl = [
            docx.Block("b0", "para", 1, 11.0, False, "Det är en tid av oro. Sverige"),
            docx.Block("b1", "para", 1, 11.0, False, "har redan ett av världens högsta skattetryck"),
            docx.Block("b2", "para", 1, 11.0, False, "När vår politik får verkan sänks priset."),
        ]
        assert docx.mid_clause_cuts(bl) == [0, 1]


class TestTheWorstFormattedDocuments:
    """Criteria 5 and 6 — the two documents the Overview named by way of damage."""

    def test_valmanifest_v_is_no_longer_one_wall(self, blocks):
        """It yielded zero headings across 51k characters."""
        doc = blocks.get("valmanifest-2022-v")
        if doc is None:
            pytest.skip("valmanifest-2022-v not extracted")
        bl = doc["blocks"]
        headings = [b for b in bl if b["role"] in ("h1", "h2", "h3")]
        assert len(headings) >= 5, f"only {len(headings)} headings"
        # and they are spread through it, not clustered on the cover: no stretch
        # of unheaded prose runs to a quarter of the document
        chars = sum(len(b["text"]) for b in bl)
        runs, run = [], 0
        for b in bl:
            if b["role"] in ("h1", "h2", "h3"):
                runs.append(run)
                run = 0
            else:
                run += len(b["text"])
        runs.append(run)
        assert max(runs) / chars < 0.25, f"largest unheaded stretch {max(runs) / chars:.0%}"

    def test_mp_2013_still_says_where_its_undecodable_headings_were(self, blocks):
        """Its section headings are set in a display font whose glyphs never map
        back to Unicode. `normalize()` drops them from the served text; the
        blocks keep them as `unreadable` so the page can say a heading is there."""
        doc = blocks.get("partiprogram-mp-2013")
        if doc is None:
            pytest.skip("partiprogram-mp-2013 not extracted")
        unreadable = [b for b in doc["blocks"] if b["role"] == "unreadable"]
        assert len(unreadable) == 50
        from aidag.extract_corpus import normalize_drops

        assert all(normalize_drops(b["text"]) for b in unreadable)


class TestEveryDocumentIsPublished:
    """Criterion 7's data half — 40 documents exported, 23 with blocks.

    That the 40 then *render* is asserted in `site/tests/corpus.test.mjs`, which
    can call the renderer; this pins the split it counts against. That the
    exported text is `normalize()` output — the same string agents read and
    `verify simulate` checks — is
    `test_corpus.py::TestSiteCorpusMatchesWhatAgentsRead`.
    """

    def test_forty_documents_reach_the_site(self):
        exported = _skip_unless(SITE_DATA_DIR / "corpus", "uv run aidag export-site")
        served = sorted(p.stem for p in exported.glob("*.txt"))
        assert len(served) == 40, served
        with_blocks = sorted(p.stem for p in (exported / "blocks").glob("*.json"))
        assert len(with_blocks) == 23
        assert set(with_blocks) <= set(served)
        # the 17 keeping the flat extraction: 16 budgetmotioner + Tidöavtalet
        rest = sorted(set(served) - set(with_blocks))
        assert len(rest) == 17
        assert all(s.startswith("budgetmotion-") or s == "tidoavtalet-2022" for s in rest), rest


class TestThePublishedCitationCountReconciles:
    """Criterion 8. `AboutPage` states the citation total and how many failed the
    verbatim check, in both languages, as prose rather than from `meta.json`.
    Prose does not go stale loudly, so the figures are pinned to the record."""

    @pytest.fixture(scope="class")
    @classmethod
    def about(cls) -> str:
        return _skip_unless(ABOUT_PAGE, "site checkout").read_text(encoding="utf-8")

    def test_the_totals_match_the_run(self, about, citations):
        assert len(citations) == N_CITATIONS
        assert sum(1 for c in citations if not c["quote"]) == N_BLANK

    def test_both_languages_print_the_total(self, about):
        assert f"All {N_CITATIONS:,} citations are checked" in about
        assert f"Alla {N_CITATIONS:,} citat".replace(",", "&nbsp;") in about

    def test_both_languages_print_the_post_repair_failure_count(self, about):
        assert f"{N_BLANK} failed the check" in about
        assert f"{N_BLANK} föll i kontrollen" in about

    def test_the_pre_repair_figure_is_gone(self, about):
        """It said "One citation failed the check". That was true of the run
        before `repair-citations` re-ran against the re-extracted corpus."""
        assert "One citation failed the check" not in about
        assert "Ett citat föll i kontrollen" not in about
