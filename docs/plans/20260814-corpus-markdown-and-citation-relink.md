# Structured corpus extraction, text cleanup, and citation relinking

> Revised after plan review (2026-08-14). Corrections applied: the frozen-bytes route
> covers p5 as well as p4; `export-site` is now an explicit step; `formatCorpusDoc()`
> survives for the 17 out-of-scope documents; the anchor locator's text basis and
> date-gated slug resolution are specified; the broken-word figure is corrected;
> `md/*.md` and the `WEAK_LIST` edits are cut.

## Overview

Re-extract the 23 cited party documents (8 valmanifest + 15 partiprogram) from their
source PDFs into **structured blocks with stable ids**, replacing the current flat
text, and **relink all 77,719 full-v4 citations** to those blocks.

Two problems solved at once, and they turn out to be the same problem:

1. **Readability.** The documents render as ragged walls. `partiprogram-m-2021` yields
   187 false subheadings, `valmanifest-2022-v` yields *zero* headings for 51k chars,
   and words sit visibly broken (`Kristde mokraterna` — 4 hits in
   `valmanifest-2022-kd.txt`, 17 each in `partiprogram-kd-2015/-2025`). Cause:
   `pdf_to_text()` discards font size, weight and column geometry, and `doctext.ts`
   then tries to guess them back from line lengths.
2. **Relinking.** Citations reach documents through `#:~:text=` fragments re-matched in
   the browser. There is no reverse index, so a document page cannot show which votes
   leaned on which line.

Structure recovery is the prerequisite for the annotation layer: once blocks carry
roles, "this citation anchors to a navigation label rather than a commitment" becomes
mechanically detectable. Measured on the KD×SfU slice: **26 of 125 citations (21%)**.

**Product outcome**: `/dokument/<slug>/` becomes a readable document with a sticky
chapter rail, where each cited line carries the votes that used it and a
kept-vs-diverged ratio bar.

### Scale of the text damage, stated accurately

`doctext.ts:29-38` is the source of the "4,565 broken words" figure and attributes it
explicitly: **4,565 sit in the budgetmotion files**, which are out of scope here;
valmanifest and partiprogram have **2 and 254** such breaks. So the in-scope character
damage is ~256 breaks, not 4,565.

This is consistent with the relink measurement (99.10% of quotes match the
re-extraction exactly) and it is **not** on its own a reason to rewrite 77,719 quotes.
The real justification for replacing the corpus is structural: blocks, roles and
reading order, which the flat text cannot carry. The quote migration is a *consequence*
of that, not the goal.

### Decisions taken (from planning)

| Decision | Choice |
|---|---|
| Corpus role | **Replace** `data/corpus/*.txt`; migrate committed quotes |
| Scope | **23 cited documents only** — budgetmotion/Tidöavtalet unchanged |
| Colour semantics | **Gap** (voted with plan / diverged), not citation validity |
| Layout | **One page + sticky nav** |
| Testing | Regular — code first, then pin behaviour with fixtures |

### Source change that must be acknowledged

The 8 manifestos currently come from SND as **plain text**
(`fetch_corpus.py:27-39`, `/vivill/file/{code}/v/2022/txt`). This plan switches them to
the **SND PDF** rendition so font metrics are available. That is a source change to the
primary p6 corpus for 8 of 23 documents — a different rendition of the same document,
not merely a cleaner one. Task 4 therefore adds a content-equivalence guard rather than
asserting equivalence.

### Non-goals

- No prompt-version bump, no `full-v5` re-run.
- Budgetmotioner (16) and Tidöavtalet keep their current extraction **and their current
  rendering path**.
- No LLM in the extraction path. A model that paraphrases a party programme is a
  credibility failure for this project; structure comes from font metrics and geometry.

## Context (from discovery)

**Files involved**
- `pipeline/aidag/corpus.py` — `normalize()`, `documents_for()`, `_text()`, `program_at()`
- `pipeline/aidag/fetch_corpus.py` — `pdf_to_text()`, `fetch_programs()`, `fetch_manifesto()`
- `pipeline/aidag/repair.py` — `best_span()`, `strip_blocked()`, atomic `.tmp` + `replace()` write
- `pipeline/aidag/blocklist.py` — `is_weak()`, bidirectional containment match
- `pipeline/aidag/export_site.py` — `export_corpus()`, translation pairing at `:149`
- `pipeline/aidag/gap.py` — `DECIDED = ("Ja","Nej")`, abstention handling
- `site/src/lib/doctext.ts`, `site/src/lib/data.ts`, `site/src/pages/dokument/[slug].astro`,
  `site/src/pageviews/DocumentPage.astro`, `CasePage.astro`
- Tests: `test_corpus.py`, `test_promptgen.py`, `test_blocklist.py`, `test_aivotes.py`,
  `test_agent_pipeline.py`

**Measurements this plan is built on** (verified)

| Fact | Value |
|---|---|
| Citations in full-v4 | 77,719 over 20,312 decisions (1 has a blanked quote → 77,718 with text) |
| Distinct quotes | 16,723; 58% cited more than once; 99.7% unique in-document |
| Relink, exact | 16,572/16,723 quotes (99.10%); 76,533/77,718 citations (98.475%) |
| Relink, + fuzzy ≥0.75 | +116 quotes → **99.505% of citations** |
| Unrecovered | 35 quotes / 385 citations, all column-interleaving |
| Header/footer strip | 1,046 lines, no prose loss |
| Cached PDF total | 24 MB (largest `valmanifest-2022-c.pdf` 9.5 MB) |

**Runs at risk**: `full-v3` is **p5 with 6,563 decisions** and is in the repo;
`full-v2` (p4) is not. `corpus.py:196` serves `normalize(raw)` for p5 — derived from the
same files — so replacing raw bytes changes p5's served text too.

**Prototype working** in scratchpad: `docx.py`, `relink_all.py`.

## Development Approach

- **testing approach**: Regular (code first, then tests)
- complete each task fully before moving to the next
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
  - unit tests for new and modified functions, success *and* error paths
  - fixtures use real corpus documents — the failures here are all real-document quirks
- **CRITICAL: all tests must pass before starting the next task**
- **CRITICAL: update this plan file when scope changes during implementation**
- run `uv run pytest tests -q` after each change (never bare `pytest`)
- start Task 5 from a clean git tree — it rewrites the committed research record

## Testing Strategy

- **unit tests**: required per task, as above
- **corpus-wide invariants**:
  - every full-v4 citation resolves to a block, is blanked, or is on the known-failure
    allowlist
  - no block contains stripped furniture (assert against the drop log)
  - paragraph fragmentation < 20% per document
  - block text round-trips to the plain-text export **modulo the documented
    `normalize()` drop set** (provenance header, digit-only lines, `_broken_font_line`)
- **verify gate**: `verify simulate` is necessarily red between Task 4 and Task 6 — that
  is what Task 5 exists to repair. The guarantee is **green at the end of Task 6 and
  thereafter, for both `full-v3` and `full-v4`**.
- **e2e**: no Playwright/Cypress suite. Verification is `cd site && npm run build` plus
  the existing deep-link regression check.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix

## What Goes Where

- **Implementation Steps**: code, tests, data regeneration in this repo
- **Post-Completion**: deploy verification, visual review, follow-on work

## Implementation Steps

### Task 1: Cache source PDFs

The 15 programme URLs are fetched live from party websites and nothing is archived. All
15 resolve today; that is luck, not a guarantee.

**Files:**
- Modify: `pipeline/aidag/fetch_corpus.py`, `pipeline/aidag/config.py`
- Create: `data/corpus/pdf/` (23 files, ~24 MB)

- [x] add `PDF_DIR = CORPUS_DIR / "pdf"`; `fetch_programs()` writes source bytes there
- [x] add `MANIFESTO_PDF_URL = "https://snd.se/sv/vivill/file/{code}/v/2022/pdf"` to
      `config.py` beside the existing SND text URL, and `fetch_manifesto_pdf()`
      (`SND_TXT_URL` moved from `fetch_corpus.py` to `config.py` so the two renditions
      sit together and the "same document, two renditions" note has one home)
- [x] extraction reads the cache when present, so re-extraction never needs the network
      — `_fetch_pdf()` returns cached bytes without a client call, and
      `source_pdf_bytes(slug)` is the read-only entry point Task 2 uses. It raises
      `FileNotFoundError` rather than fetching, so an extraction run can never silently
      pick up a *newer* edition than the corpus was built from.
- [x] decide and record PDF commit policy: **COMMIT**. 23 files, 24 MB, matching the
      already-committed `tidoavtalet-2022.pdf`. Decisive point: the 15 programme URLs
      are live party-site links and every one of those parties has already replaced the
      pinned edition once (`config.py`'s own "TRAP" note) — the archive is the whole
      reason the corpus is committed. Verified on arrival: `pdf_to_text()` over the 15
      cached programme PDFs reproduces all 15 committed `.txt` bodies **byte for byte**,
      so the cache holds the exact documents full-v3/full-v4 were generated from.
- [x] no new tests here (no behaviour yet); `NoTextLayer` tests live with Task 2
- [x] run `uv run pytest tests -q` — must pass before Task 2 (196 passed)

➕ Confirmed against the cache, for Task 2/3: `valmanifest-2022-c` is 38 pages with 388
raw chars (10/page) — the vector-outline document the `NoTextLayer` + `blocks_from_text`
path exists for, and the only one anywhere near the 200 chars/page floor. The next
lowest is `valmanifest-2022-kd` at 946/page.

### Task 2: Add `pipeline/aidag/docx.py` — structured extraction

Order is load-bearing: **strip furniture → split blocks → assign roles**. Furniture must
go before blocking or a page number lands inside an anchored block.

**Files:**
- Create: `pipeline/aidag/docx.py`, `tests/test_docx.py`

- [x] `read_lines()` — page, layout-block index, size, font, bold, bbox via
      `get_text("dict", sort=True)`; keep the soft hyphen (stripping it before the join
      is what produces `Kristde mokraterna`). Style is taken from the line's *longest*
      span, so a drop cap cannot type the line. Bold reads PyMuPDF's flag with a
      font-name fallback for the PDFs that never set it.
- [x] `NoTextLayer` raised when extracted chars/page < 200
- [x] `blocks_from_text()` — degraded path producing `para` blocks from flat text, for
      `valmanifest-2022-c` (38 pages, 241 extractable chars — vector outlines) so it
      still gets blocks and a document page. One block per line: SND's plain-text
      rendition already puts one paragraph per line (median 208 chars, no wrapping).
- [x] `strip_running()` — drop only when BOTH in the top/bottom 10% band AND the
      digit-normalized signature repeats on ≥ max(3, 20% of pages); plus bare page
      numbers in the band. Signature counts **distinct pages**, not lines (V-2024
      paints its header twice per page), and includes `(size, bold)` — see ⚠️ below.
- [x] `split_blocks()` — new block on page change, style change, gap > 1.65× median
      leading, bullet/numbered start, contents entry; layout-block change splits **only
      when** the previous line ended a sentence or the gap exceeds 1.2× leading
      (PyMuPDF emits per-paragraph blocks in KD, per-line blocks in L-2023). A
      backwards y-jump also splits — it is a column or region change.
- [x] `join_lines()` — close soft-hyphen and explicit-hyphen breaks with no space
- [x] `clean_line()` — NFC, ligature folding, zero-width/NBSP, stray `¬`, dot leaders
- [x] write tests: `NoTextLayer` raised for C and not for KD; `blocks_from_text` yields
      non-empty blocks for C
- [x] write tests: `strip_running` drops `Informationsklass: Intern`, keeps a repeated
      prose phrase in body position
- [x] write tests: `join_lines` (soft hyphen / explicit hyphen / plain wrap)
- [x] write tests: `split_blocks` neither fuses two commitments nor shreds a wrapped
      paragraph
- [x] run tests — must pass before Task 3 (235 passed; 39 new in `tests/test_docx.py`)

⚠️ **Deviation, recorded**: the furniture signature is `(size, bold, digit-normalized
text)`, not text alone as specified. On text alone V-2024 loses prose: its 6pt running
header carries the chapter name, so the chapter's own 30pt title — which sits inside the
top band and repeats that name once — inherits the header's 19-page count and is dropped.
With style in the signature the title survives and the header still goes. Corpus-wide the
rule now drops **1,056 lines**, against the 1,046 the prototype measured, and every drop
is ≤ 60 chars and is a header, footer or page number (asserted per document in the
tests). Residual leak: V-2024's 6pt `Våra svar` header spans only 5 pages against a
threshold of 6, so it survives as a stray 6pt block — Task 3's style clusters should
give it a non-`para` role.

➕ Confirmed for Task 3, from the real extraction:

- **Role instability is exactly as described.** kd-2025's `2.1 Demokrati` subheadings are
  12.0pt against an 11.0pt body — ratio 1.09, under the 1.12 cut — so they come out
  `para`. Size ratios cannot fix this; style clusters can.
- **Column interleaving is visible in the output.** `valmanifest-m` p6 fuses
  `• Kraftigt sänka kostnaden för att anställa långtidsarbetslösa` with the next
  column's `Moderaterna kommer att:`, and the same page's chart axis emits
  `0 Malta Irland`. Both are `detect_columns()` work.
- **Rotated margin furniture** (`Valmanifest 2022`, once per page in `valmanifest-m`)
  sits mid-page, so the band rule cannot see it. It lands as a stray `para`. Candidate
  for a `caption`/`label` role rather than a new stripping rule.
- **Intra-word spaces from tracking** (`2.1 Dem okrati` in kd-2025, 17 similar) are
  literal space characters in the content stream, not a hyphen artifact — a separate
  defect from `Kristde mokraterna`, fixable only at char level (`rawdict` gives per-char
  x-advance). Left alone: it is pre-existing damage that today's committed text also
  carries, so touching it would move quotes Task 5 has to migrate. Not in scope.

### Task 3: Fix role assignment and column reading order

The two defects the POC review exposed — the difference between a demo and a pipeline.

**Role instability.** Across three splitter iterations KD's back-cover topic labels
classified as `para` → `toc` → mixed. They are one semantic list at a single style
(10.9pt ExtraBold), but roles keyed off block length and run position. Size-ratio
thresholding cannot see them: 10.9/10.0 = 1.09, under the 1.12 heading cut.

**Column interleaving.** All 35 unrecovered quotes come from multi-column pages where
`sort=True` interleaves columns: `"Kollektivtrafik … krävs Många på land"`.

**Files:**
- Modify: `pipeline/aidag/docx.py`, `tests/test_docx.py`

- [x] `style_clusters()` — char-volume-ranked clusters; body = largest. Keyed on
      `(size, font)`, **not** `(size, bold)` — see ⚠️ below.
- [x] rewrite `assign_roles()` to map clusters → roles rather than size ratios; add a
      `label` role for a minor bold cluster at or below body size
- [x] `detect_columns()` — recursive XY-cut per page (gutter first, one horizontal band
      cut at a time), rotated text lifted out of the flow; single-column pages are
      returned untouched and 7 of the 22 extractable documents verify that byte for byte
- [x] restrict `mark_toc()` to near-body-size runs (a cover page is legitimately 4+
      large titles in a row — this mislabelled 14 of KD's 45 blocks)
- [x] write test: KD's 5 back-cover labels all get the same role, and it is not `para`
- [x] write test: `mp-2013` / `valmanifest-m` sample pages produce no interleaved text
- [x] write test: single-column block sequence unchanged by `detect_columns`
- [x] record the residual known-failure allowlist — `data/corpus/known-unrecovered.json`,
      **12 quotes / 119 citations**, down from 35 / 385. Not empty; see ⚠️ below.
- [x] run tests — must pass before Task 4 (272 passed; 37 new in `tests/test_docx.py`)

**Measured against full-v4, whitespace collapsed** (the basis Task 5 migrates on):

| | Task 2 | Task 3 |
|---|---|---|
| Citations located exactly | 76,525 / 77,718 (98.465%) | **77,079 (99.178%)** |
| Distinct quotes unlocated | 162 | 105 |
| ...of those, *structural* (word order the extraction never produced) | 19 | **12** |
| Paragraphs ending mid-clause, `valmanifest-m` | 58.5% | **25.3%** |
| ...`partiprogram-kd-2015` | 55.0% | **32.0%** |
| ...`partiprogram-kd-2025` | 42.0% | **30.2%** |

The other 93 unlocated quotes are character damage the *old* text carries and the new
extraction fixes — `hälso och` for `hälso- och`, `storregio nala` for `storregionala`.
They are Task 5's to migrate, not Task 3's to match.

⚠️ **Deviation 1, recorded**: clusters are keyed `(size, font)`, not `(size, bold)` as
specified. On `(size, bold)` KD's back-cover labels are lost: 10.9pt Barlow-ExtraBold
against 11.0pt Barlow-SemiBold prose is the same weight 0.9% apart, so the size tolerance
that has to exist (valmanifest-m reports one 11pt body as 11.0/11.1/11.2/11.3) folds the
labels into the prose and roles them `para` — exactly the failure the task exists to fix.
The font name separates them; `bold` is kept on the cluster as a derived attribute.

⚠️ **Deviation 2, recorded**: the allowlist is **not empty — 12 quotes / 119 citations**,
all but two in `valmanifest-m`. Each is a page where a section heading belonging to a
*figure* sits directly beneath a two-column text block, 9.2pt below it. Reading order
there is not determined by the geometry this module extracts: separating the heading from
the columns needs the figure's bounds, and PyMuPDF's image blocks are discarded at
`read_lines`. Listed rather than tolerated, per the plan; Task 7's gate reads the file.

➕ **Added beyond the checkboxes**: `_runs_on()` closes paragraphs broken by a page
boundary. Task 10 assigns the fragmentation target to this task, and page splits were
most of what the figure measured — 312 of kd-2015's 764 paragraphs ended mid-clause at a
page foot. Conservative by construction: same face, lower-case opener or a hyphen, no
bullet, no contents entry, adjacent pages. It moved 12 of 22 documents under the 20% gate
and left the relink rate unchanged to four decimals.

⚠️ **Still over the Task 10 20% gate**: `kd-2015` 32.0%, `kd-2025` 30.2%, `sd-2019`
24.8%, `valmanifest-m` 25.3%, `mp-2013` 17.4% (passing). The residual in both KD
documents is dominated by their **glossaries** — 237 marginal terms in 2015, an
`Ordförklaringar` appendix in 2025 — which are dictionary entries with no terminal
punctuation and are not fragments at all. Task 10 should either exclude `label` blocks
and glossary appendices from the metric or restate it; the metric as written cannot
distinguish them from broken paragraphs.

### Task 4: Freeze current bytes, regenerate the corpus, re-export

`corpus.py:188-192` serves **raw** bytes for `prompt_version < "p5"`, and `corpus.py:196`
serves `normalize(raw)` for p5+ — both derived from the files being replaced. The run
actually in the repo is **full-v3, p5, 6,563 decisions**. So the frozen route must cover
**everything below p6**, not below p5.

`tests/test_corpus.py:131-142` asserts every exported site document equals
`normalize(raw)` for ≥40 files, so regeneration without re-export fails this task's own
gate.

**Files:**
- Create: `data/corpus/frozen/` (byte-identical copies), `data/corpus/blocks/<slug>.json`
- Modify: `data/corpus/<slug>.txt` (23 files), `pipeline/aidag/corpus.py`,
  `pipeline/aidag/cli.py`, `tests/test_corpus.py`

- [ ] copy current `data/corpus/*.txt` to `data/corpus/frozen/` unchanged
- [ ] route `_text()` to `frozen/` when **`prompt_version < "p6"`**, so p4 *and* p5 keep
      verifying against the bytes they were generated from
- [ ] add `aidag extract-corpus [--slug X] [--force]` writing `blocks/*.json` and the
      derived `.txt`
- [ ] derive `.txt` **from the blocks** so text and structure cannot drift; keep the
      provenance header (`export_site.py:62-66` and `doctext.parseProvenance` expect it)
- [ ] restate the normalize expectation precisely: `normalize()` is a **no-op on prose
      characters**, not a no-op — it still strips the header, digit-only lines and
      `_broken_font_line` lines
- [ ] content-equivalence guard for the 8 manifestos: word count of the PDF extraction
      within ±5% of the SND `/txt`, else fail (the source changed rendition)
- [ ] run `uv run aidag export-site --run-id full-v4`
- [ ] write test: `.txt` round-trips from `blocks/*.json` modulo the drop set
- [ ] write test: p5 path reads `frozen/`, p6 path reads the regenerated file
- [ ] check `tests/test_corpus.py:55-65` (asserts `"anslöt sig"` and `"Nato"` in
      `partiprogram-mp-2025.txt`) still holds after column reordering; update if it is a
      fixture artifact rather than a real invariant
- [ ] run tests — must pass before Task 5

### Task 5: Migrate committed citation quotes

Quotes were produced against the old text; cleanup changes some prose characters. The
rewrite must be auditable — this edits the research record. Reuse the `repair.py`
precedent (`quote_ej_verifierad`, atomic `.tmp` + `replace()`).

**Files:**
- Create: `pipeline/aidag/migrate_quotes.py`, `tests/test_migrate_quotes.py`
- Modify: `pipeline/aidag/cli.py`, `data/results/simulations/full-v4/*.jsonl`

- [ ] `aidag migrate-quotes --run-id full-v4 [--dry-run]`
- [ ] resolve each citation's document with **`corpus.documents_for()` for the
      decision's `(parti, datum)`** — a citation records only the document *class*, and
      L has three programme editions, V/KD/S/SD/MP two each
- [ ] exact substring first; on miss `best_span` against candidate blocks chosen by an
      inverted token index (whole-document `best_span` did not finish in 10 min; the
      prefilter runs in seconds)
- [ ] fuzzy hit ≥0.75 → rewrite `quote`, stash original in `quote_fore_migrering`, flag
      `citat_migrerat`; never overwrite an existing sidecar on a second run
- [ ] failure → **leave the quote untouched**, flag `citat_ej_migrerat`; never blank it
- [ ] skip citations whose `quote` is already blank (`repair._mark_unverifiable`)
- [ ] write atomically via `.tmp` + `replace()`
- [ ] `--dry-run` reports per-document exact/fuzzy/failed and changes nothing
- [ ] write tests: exact passthrough; fuzzy rewrite sets sidecar + flag; failure leaves
      quote intact; re-run is idempotent; a KD 2023 vote resolves to
      `partiprogram-kd-2015`, not `-2025`
- [ ] run tests — must pass before Task 6

### Task 6: Re-run repair and verify, and check translation alignment

`repair-citations` calls `strip_blocked()` (`repair.py:134`), which **removes**
citations. English translations pair **positionally** (`export_site.py:149`, consumed at
`CasePage.astro:535` as `dEn?.citations?.[i]`), so any length change silently renders
the wrong English quote against the wrong Swedish one.

**Files:**
- Modify: `data/results/simulations/full-v4/*.jsonl`, site data

- [ ] record per-decision citation-list lengths **before** repair
- [ ] run `uv run aidag repair-citations --run-id full-v4`
- [ ] assert citation-list lengths unchanged; if any changed, list affected cids and
      re-run translation for them rather than shipping misaligned English
- [ ] decide and record: `citat_migrerat` rows keep English translated from the
      pre-migration wording (accept, since the change is sub-quote) or are re-translated
- [ ] run `uv run aidag verify simulate --run-id full-v4` — must be green
- [ ] run `uv run aidag verify simulate --run-id full-v3` — must be green (frozen route)
- [ ] compare before/after `citat_korrigerat`, `citat_ej_verifierat`, `citat_svagt`,
      `citat_blockerat`; investigate any increase
- [ ] confirm `citat_ej_migrerat` matches the Task 5 dry-run
- [ ] run `uv run aidag export-site --run-id full-v4`
- [ ] run full suite `uv run pytest tests -q`

### Task 7: Build the anchor index

Quote is the key; offsets are derived at build time. That is what makes format and links
independent — re-extraction re-runs the locator instead of breaking links.

**Files:**
- Create: `pipeline/aidag/anchors.py`, `tests/test_anchors.py`
- Modify: `pipeline/aidag/cli.py`, `pipeline/aidag/export_site.py`

- [ ] `aidag build-anchors --run-id full-v4` → `data/results/anchors/full-v4/<slug>.json`
- [ ] **locator text basis**: match against block text normalized with
      `corpus.normalize()` semantics, so the locator and `verify simulate` agree
- [ ] resolve the target document via `documents_for()` for `(parti, datum)`, as Task 5
- [ ] per anchor: `block_id`, offset in block, refs of
      `(votering_id, parti, verdict, tier, utskott, datum, svag)`
- [ ] verdict from `rost` vs `party_positions`: `kept` | `diverged` | `avstar` |
      `franvarande`. `gap.py:121` scores only `("Ja","Nej")`; abstention is a floor
      tactic the plan was never asked to predict and `Frånvarande` is excluded
      throughout (`analytics.py:77,160,177`). Note in code that `rost` is derived from
      `hallning` at ingest, so this is the gap, not vote agreement
- [ ] **fail the build** on any quote that fails to locate and is neither blank nor
      flagged `citat_ej_migrerat` — silent link-rot is the failure this design prevents
- [ ] export blocks + anchors to `site/src/data/corpus/`
- [ ] **page-weight decision, up front**: inline aggregate counts and the ratio bar;
      full ref lists behind a per-document JSON fetch. `valmanifest-2022-sd` has 9,007
      refs — inlining 7 fields each would be multi-MB of static HTML
- [ ] write tests: anchor resolves to the correct block; unlocatable quote fails the
      build; blanked quote does not; verdict derivation matches `party_positions`;
      date-gated slug resolution
- [ ] run tests — must pass before Task 8a

### Task 8a: Render documents from blocks (parity with today)

**Files:**
- Modify: `site/src/pageviews/DocumentPage.astro`, `site/src/lib/data.ts`,
  `site/src/lib/doctext.ts`

- [ ] render from `blocks/*.json` roles when a block file exists for the slug
- [ ] **keep `formatCorpusDoc()` as the fallback branch** — `[slug].astro:5` builds a
      page for every slug from `listCorpusDocs()` (all 40), and only 23 have blocks.
      Deleting it would regress the 16 budgetmotioner + Tidöavtalet to a ragged wall.
      Retire it only when the budgetmotion parser lands
- [ ] verify existing `#:~:text=` deep links still resolve
- [ ] write tests for the `data.ts` block loader (present / absent slug)
- [ ] `cd site && npm run build` succeeds
- [ ] run tests — must pass before Task 8b

### Task 8b: Annotation layer and chapter rail

**Files:**
- Modify: `site/src/pageviews/DocumentPage.astro`, `site/src/i18n/ui.ts`

- [ ] sticky chapter rail from `h1`/`h2` blocks with per-chapter citation counts
- [ ] per-cited-block: use count, kept/diverged ratio bar, tier breakdown, ref list
      loaded on demand
- [ ] colour the gap axis accent-blue vs deep-gold — **not** `--ja`/`--nej`, which are
      the vote colours; red would assert "broken promise", which `gap.py` explicitly
      refuses ("a divergence is the signal, not an error")
- [ ] state in the rail that a party is read against its own manifesto alone, so a
      governing party following a coalition agreement diverges *by design*
- [ ] mark citations landing on `label`/`toc` blocks — a topic label promises nothing
- [ ] add sv/en i18n strings for all new UI
- [ ] `cd site && npm run build`; check page weight for `valmanifest-2022-sd`
- [ ] run tests — must pass before Task 9

### Task 9: Report the topic-label finding

Reporting only. The `WEAK_LIST` route was **cut on review**: `blocklist.py:23-26`
requires phrases "long and distinctive enough that either-direction containment cannot
catch a genuine policy quote", the match is bidirectional (`blocklist.py:139`), and
`document` is a *class* not a slug — so a short label like `EN STRAM MIGRATION` would be
tested against all eight parties' programmes. Task 8b already marks these per-block,
per-document and exactly.

**Files:**
- Create: `pipeline/aidag/anchors.py` reporting subcommand or a script
- Modify: `docs/` findings note

- [ ] report corpus-wide counts of citations landing on `label`/`toc` blocks, by party
      and document
- [ ] record the result as a finding; if any phrase genuinely meets the
      length/distinctiveness bar, propose it for `WEAK_LIST` separately with a
      cross-party false-positive test
- [ ] write tests for the reporting aggregation
- [ ] run tests — must pass before Task 10

### Task 10: Verify acceptance criteria
- [ ] every full-v4 citation resolves to a block, is blank, or is flagged
      `citat_ej_migrerat`
- [ ] `verify simulate` green for **both** `full-v3` and `full-v4`
- [ ] no block contains stripped furniture (assert against the drop log)
- [ ] paragraph fragmentation < 20% for every document (currently `kd-2025` 40%,
      `valmanifest-m` 39% — must be fixed by Task 3)
- [ ] `valmanifest-2022-v` no longer a single wall (currently median block 487 chars)
- [ ] `mp-2013`'s 43 unreadable headings still surfaced as explicit placeholders
- [ ] all 40 document pages render (23 from blocks, 17 via `formatCorpusDoc`)
- [ ] reconcile the citation count in `site/src/pageviews/AboutPage.astro:344`
      ("All 77,719 citations") with the post-repair total
- [ ] run full suite `uv run pytest tests -q`; `cd site && npm run build`

### Task 11: [Final] Update documentation
- [ ] update `CLAUDE.md`: extraction pipeline, `frozen/` split, new commands
- [ ] update `README.md` if the corpus layout is described there
- [ ] move this plan to `docs/plans/completed/`

## Technical Details

**Corpus layout after this change**

```
data/corpus/
  pdf/<slug>.pdf              source bytes, cached (Task 1)
  frozen/<slug>.txt           pre-change text; serves prompt_version < p6
  blocks/<slug>.json          [{id, role, page, size, bold, text}]
  <slug>.txt                  derived from blocks; what agents read, what verify checks
  known-unrecovered.json      residual unlocatable quotes (Task 3); read by Tasks 5 and 7
```

`md/*.md` was **cut on review**: agents read `.txt`, verify reads `.txt`, the site
renders from `blocks/*.json`. A third representation with no consumer is a sync burden.
`to_markdown()` stays in `docx.py` as a debugging dump, writing outside `data/`.

**Block roles**: `h1` `h2` `h3` `para` `bullet` `label` `toc` `caption` `unreadable`

**Anchor record**

```json
{"quote": "Stram migrationspolitik krävs…",
 "block_id": "b0041", "offset": 0,
 "refs": [{"votering_id": "…", "parti": "KD", "verdict": "diverged",
           "tier": "extrapolated", "utskott": "SfU", "datum": "2023-…"}]}
```

`quote_hash` was cut — if the quote is the key, a hash beside it is a second key for the
same thing.

**Why quote-as-key, not stored offsets.** Offsets are what re-extraction invalidates,
and re-extraction is half of this work. The locator is deterministic (99.10% exact,
99.7% of quotes unique in-document), so offsets are recomputed at build time.

**Known-failure allowlist.** `data/corpus/known-unrecovered.json`, so the Task 7 gate
distinguishes "known and accepted" from "newly broken". Task 3 took it from the measured
35 quotes / 385 citations to **12 quotes / 119 citations** (`valmanifest-m` 10,
`valmanifest-kd` 1, `partiprogram-v-2024` 1); `valmanifest-s` and `mp-2013` are clear.
It is a ratchet: `tests/test_docx.py::TestKnownUnrecovered` fails if the list grows or if
a listed quote starts resolving.

## Post-Completion

**Manual verification**
- read `partiprogram-kd-2015` (172 pp) and `valmanifest-2022-v` end to end on desktop
  and mobile — the worst-formatted documents are the real test
- confirm the gap bars do not read as a "broken promise" scoreboard, especially for
  M/KD/L where divergence is expected by design
- spot-check ~20 anchors against their source PDFs for correct attribution

**Deployment**
- Cloudflare runs `cd site && npm ci && npm run build` under **dash** — no bash-only
  syntax in any build script
- watch build time and bundle size as anchor data lands

**Follow-on work (out of scope)**
- budgetmotion HTML parser using `Rubrik1/2/3numrerat`, `ListaLinje`, `TOC1-3` — 16
  documents, readability only; retires `formatCorpusDoc()` when done
- HTML editions as a source for current programmes (V-2024 verified complete at 0.98
  word ratio; MP-2025 has an 18-chapter HTML edition) — needs a content-hash guard,
  since those URLs track whatever is current while `config.py` pins versions
- KD-2015's 245-entry marginal glossary could render as an aside
