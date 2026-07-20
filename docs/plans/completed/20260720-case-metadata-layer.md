# Case Metadata Layer (hybrid: deterministic + grounded cheap-LLM)

## Overview
Pre-build a rich, per-case **metadata layer** for the AI Riksdag site so a reader instantly
understands each vote, cases become searchable/filterable by topic, the data is exported as clean
open data, and a **de-leaked "agent view" is prepared** for a later (separate) simulation re-run.

Today most votes show only a title; the report-level `utsknotis` lead is generic for bundle votes
("cirka 50 förslag om X"), reservations are stored label-only, and the anonymous prompt leaks
motion-author party tags. This layer fixes the human-facing gaps now and stages the agent-facing
fix without touching the live experiment.

**Decoupled by design:** this plan changes NOTHING in `promptgen.py` and does not re-run the
simulation. It produces `agent.{subject,at_stake}` (party-blind, pre-decision) as data only. Wiring
that into the prompt + re-running is a separate go/no-go (Phase 3, out of scope here).

Approach **A (hybrid)**: deterministic extraction for factual/structured fields (zero LLM), plus a
grounded **cheap-model (Haiku 4.5)** synthesis for the subject / what's-at-stake / subtopics —
generated as **parallel batches over all ~2,539 cases** via the Claude Code subagent workflow (no
API key), mirroring the existing translation pipeline.

## Context (from discovery)
- **Files/components involved:**
  - New: `pipeline/aidag/metadata.py`, `scripts/metadata_batch_workflow.js`, `tests/test_metadata.py`
  - Modify: `pipeline/aidag/cli.py`, `pipeline/aidag/export_site.py`, `site/src/pageviews/CasePage.astro`,
    `site/src/pageviews/CaseBrowser.astro`, `site/src/lib/data.ts`, `site/src/i18n/ui.ts`
  - Storage (new, committed): `data/results/metadata/cases.jsonl` (run-independent, append-only)
- **Patterns to mirror (studied this session):**
  - `pipeline/aidag/translate.py` — `prepare/ingest/status`, run-independent `cases.jsonl`, checkpoint = ids present, request text travels inside each agent's file, idempotent dedupe-on-id ingest, `verify_translations` generator.
  - `scripts/translate_batch_workflow.js` — loader agent reads manifest → `parallel()` fan-out one agent per request file with a StructuredOutput schema; fail-fast on missing `args.manifestPath`.
  - `pipeline/aidag/export_site.py` — per-case payload build + `index.append({...})` (the `sammanfattning`/`miss` fields added this session mark the merge points; index is client-fetched → keep lean).
  - `site/src/pageviews/CasePage.astro` — the `.summary` lead added this session (using `utsknotis`) is what `metadata.subject` replaces; EN "not translated" notice pattern at the header.
  - `site/src/pageviews/CaseBrowser.astro` — paginated table, client search over title fields, styles `is:global` under `.cb`.
  - `tests/test_promptgen.py` — leakage golden tests; reuse the party-tag regex `\((S|M|SD|C|V|KD|MP|L)\)` for the agent-view de-leak assertions.
- **Dependencies:** `polars` (cases.parquet), typer CLI, the Workflow subagent path. `utsknotis`, `forslag_text`, `alternatives`, `references` already in `data/processed/cases.parquet`.

## Development Approach
- **Testing approach: Regular** (code first, then tests) — but tests are a **required deliverable of every task**, matching the repo's pytest style (`tests/`).
- Each task = one logical unit; complete fully (incl. tests passing) before the next.
- Every code task adds/updates unit tests (success + error/edge). Deterministic extraction and the
  **agent-view de-leak** get golden tests.
- **CRITICAL:** all tests pass before starting the next task.
- **CRITICAL:** keep this plan in sync — mark `[x]` immediately, add `➕` for new tasks, `⚠️` for blockers.
- Do **not** modify `promptgen.py`. `verify simulate` must stay green. Maintain backward compatibility (missing metadata → pages fall back to `utsknotis`, exactly as today).

## Testing Strategy
- **Unit tests (`tests/test_metadata.py`):** deterministic extractor (type/policy_area/parties/counts on known cases), `_case_unit` shape, `validate_metadata` accept/reject, `ingest` idempotency + dedupe, and **golden de-leak tests** on the agent view (no party tags, no beteckning/date, no post-decision outcome words).
- **Pipeline verify:** new `aidag verify metadata` gate (records align with sources; agent view clean) — run locally, added to the hermetic-where-possible verify set.
- **Site build gate:** `cd site && npm run build` (astro build; `astro check` needs interactive install). UI changes verified by build + a rendered-HTML spot check (the project has no Playwright e2e; the built `/fall/<id>/` and `/fall/` HTML are grepped/inspected as the e2e substitute).
- **Generation run** (Task 6) is validated by spot-checking ~6 varied cases (bundle, proposition, budget, reservation, migration, defence) for factual, on-topic, party-blind agent views.

## Progress Tracking
- `[x]` when done · `➕` newly discovered task · `⚠️` blocker · update on scope change.

## What Goes Where
- **Implementation Steps** (`[ ]`): all code, tests, the generation run, site wiring, docs.
- **Post-Completion** (no checkboxes): the publish/push to the PUBLIC repo, and the separate Phase 2 (fetch) / Phase 3 (leak-quantify + re-run) decisions.

## Implementation Steps

### Task 1: Deterministic extractor + utskott→policy_area map

**Files:**
- Create: `pipeline/aidag/metadata.py`
- Create: `tests/test_metadata.py`

- [x] create `pipeline/aidag/metadata.py` with paths (`METADATA_DIR`, `cases_path()`), `_read_jsonl`, `load_metadata()` (dict keyed by `votering_id`) — mirroring `translate.py`
- [x] add `UTSKOTT_AREA: dict[str, dict]` mapping every committee code (16 in data: SoU, MJU, JuU, UbU, CU, NU, TU, KU, SfU, UU, KrU, SkU, AU, FöU, FiU, UFöU) → `{"code": <stable slug>, "sv": …, "en": …}`. **`policy_area` stored as the stable `code`**; `area_for()` is case-insensitive w/ `OTHER_AREA` fallback; `policy_area_labels()` surfaces sv/en to the site. FöU+UFöU fold to `defense`.
- [x] implement `extract_deterministic(case) -> dict`: `type`, `policy_area` (code), `committee`, `n_motions` (typ==mot refs), `n_reservations` (alts minus `utskottet`), `is_budget`, `parties_involved`. Precedence: **budget** (utgiftsram/utgiftstak/anvisar anslag/finansplan/rambeslut/`FiU1`) → **proposition** ("antar/godkänner regeringens förslag"/"bifaller proposition") → **motion** ("avslår/bifaller motion") → **other**. HTML entities unescaped before matching. `type`/`parties` best-effort.
- [x] `parties_involved` (DISPLAY ONLY): PRIMARY = reservation `source_partier`; fallback scrapes `forslag_text` author clauses via reused `promptgen.AUTHOR_RE`.
- [x] write tests: `type` on proposition/motion-bundle/budget + **mixed** (→proposition) + budget-precedence-over-motion, real votering_ids; `policy_area` for 3 committees; `parties_involved` from `source_partier`; `n_reservations`
- [x] write tests for edge cases: empty/missing `forslag_text`, unknown committee → "ovrigt"/Other, author-clause fallback, HTML-entity unescape
- [x] run tests — must pass before Task 2 ✅ 21 passed

### Task 2: Grounded request units + `prepare()` / `status()`

**Files:**
- Modify: `pipeline/aidag/metadata.py`
- Modify: `tests/test_metadata.py`

- [x] add `_case_unit(case)` with **two grounding blocks**: `display_src` (party-aware: rubrik, utskott, summary=utsknotis, truncated forslag_text, reservation texts, ≤15 ref titles) + `agent_src` (SCRUBBED via `promptgen.scrub_text(..., "anonymous")` over forslag/reservations/ref-titles; structured `source_partier`/`parties_involved` omitted). Neither block includes post-decision `notis`.
- [x] add `INSTRUCTIONS`: from `display_src` → human `subject{sv,en}` + `subtopics[]` + `at_stake{sv,en}` (= why-it-matters, NOT literal Ja/Nej — compact.py owns those). From `agent_src` ONLY → `agent{subject,at_stake}` (party-blind, no outcome, pre-decision). States guarantee = scrubbed input; validator = tripwire.
- [x] implement `_pending()` + `prepare(batch_size=400, per_request=12)` writing request files + `batch-NNN.json` manifest that **OMITS `run_id` and `kind`** (run-independent, single-kind). Default fans out over ALL pending.
- [x] implement `status()` (done/pending counts)
- [x] write tests: `_pack`; `_case_unit` shape; `prepare` writes N reqs + manifest with matching `n_items` (asserts no `run_id`/`kind`); `_pending` excludes ingested ids; **golden `agent_src` de-leak** (author names, party tag, full party name, doc ref, FORBIDDEN_PATTERNS all gone; the scrub fired) + no `notis` in either block
- [x] run tests — must pass before Task 3 ✅ 28 passed

### Task 3: `ingest()` + validation (grounding + de-leak asserts) + `verify_metadata`

**Files:**
- Modify: `pipeline/aidag/metadata.py`
- Modify: `tests/test_metadata.py`

- [x] de-leak checks REUSE existing controls: lazy-import `promptgen.FORBIDDEN_PATTERNS` + `AUTHOR_RE` + `DOCREF_RE` (bare doc numbers "2022/23:85" that FORBIDDEN_PATTERNS misses); `PARTY_TAG_RE = \((S|M|SD|C|V|KD|MP|L)\)`; `PARTY_NAMES_RE` from `config.PARTIES[*]["name"]`; enumerated past-tense `OUTCOME_RE` (sa ja/nej, biföll, avslog, godkändes, antogs, beslutade, röstade igenom, röstades ned, fick majoritet, vann omröstningen, avslag på propositionen)
- [x] implement `validate_metadata(rec, unit=None)`: non-empty `subject.sv/.en`, `at_stake.sv/.en`; `subtopics` a list; `agent.subject`/`agent.at_stake` non-empty AND clean (raise otherwise). Optional `unit` = votering_id alignment. Tripwire, not guarantee.
- [x] implement `ingest(input_path, model)`: read `{cases:[...]}`, validate each, merge server-side `extract_deterministic`, dedupe on `votering_id`, append with `model`+`collected_at` (idempotent; skips leaky + unknown ids)
- [x] implement `verify_metadata(run_id=None)` generator: yields (records valid + agent view clean; every id in parquet; deterministic fields align with source)
- [x] write tests: `validate_metadata` rejects party tag / full party name / author clause / outcome word / bare docref / beteckning / empty fields / non-list subtopics / id mismatch; accepts clean
- [x] write tests: `ingest` merges deterministic + dedupes + idempotent + skips leaky/unknown; `verify_metadata` passes clean, flags planted leak + deterministic drift
- [x] run tests — must pass before Task 4 ✅ 46 passed

### Task 4: CLI commands + `verify metadata` stage

**Files:**
- Modify: `pipeline/aidag/cli.py`
- Modify: `tests/test_metadata.py`

- [x] add `metadata-prepare` (`--batch-size`, `--per-request`), `metadata-ingest` (`--input`, `--model`; kept `--input` for consistency with `translate-ingest`/`agent-ingest`), `metadata-status`
- [x] wire `metadata` into the `stages` dict in `verify.py` (run-independent → into `verify all`/CI for free) + updated the `verify` help string
- [x] write a test invoking `verify metadata` returns 0 on a clean fixture jsonl and non-zero on a leaky one
- [x] run tests — must pass before Task 5 ✅ 48 passed; live `aidag verify metadata` green (0 records), `metadata-status` = 0/2539

### Task 5: Parallel batch workflow script (cheap model)

**Files:**
- Create: `scripts/metadata_batch_workflow.js`

- [x] mirror `scripts/translate_batch_workflow.js`: `meta` block, `METADATA_UNITS_SCHEMA` (`{votering_id, subject{sv,en}, at_stake{sv,en}, subtopics[], agent{subject,at_stake}}`), and a `MANIFEST_SCHEMA` that **drops `run_id` and the `kind` enum**
- [x] fail-fast guard checks `args.manifestPath.includes('metadata')` (NOT `'translate'`); tolerates stringified args; loader agent reads the manifest + transcription count check
- [x] `parallel()` fan-out one agent per request file with `model: "haiku"`, `phase: "Metadata"`, units schema; prompt: read ONLY that file, follow `instructions`, one unit per input copying `votering_id`, agent object from `agent_src` only (party-blind), structured output only
- [x] returns `{ cases: <flattened units> }`
- [x] added `TestWorkflowScript` guard tests (fail-fast on metadata, units-schema fields, manifest drops run_id/kind, haiku model); `node --check` passes; verify (manual) in Task 6

### Task 6: Generate metadata for ALL cases (parallel batch run)

**Files:**
- Modify: `data/results/metadata/cases.jsonl` (generated)

- [x] `metadata-prepare --batch-size 400 --per-request 12` → one manifest, 212 Haiku agents (12 cases each)
- [x] `Workflow(...)` full run: 212/213 agents done, 1 API-dropped agent → checkpoint left its cases pending; extracted `json['result']` → temp file
- [x] `metadata-ingest --model claude-haiku-4-5`: full run ingested 2494/2516, **22 rejected by the de-leak tripwire for past-tense OUTCOME words** ("avslogs"/"antogs"/"godkändes") in the agent view — the tripwire worked as designed
- [x] ROOT CAUSE + FIX: Haiku stylistically wrote the agent view in past tense for reject/approve cases. Tightened `INSTRUCTIONS` to force PRESENT-tense topic phrasing (forbid avslogs/antogs/godkändes/biföll…); re-prepared the 40 pending (18 dead-agent + 22 rejected), re-ran (batch-003, 8 agents) → **40/40 ingested, 0 skipped**
- [x] `uv run aidag verify metadata` — GREEN: 2539 records, 0 invalid/leaky, 0 unknown, 0 misaligned
- [x] backfilled until `metadata-status` = **2539/2539 done, 0 pending**
- [x] spot-checked budget/migration(SfU→pension)/defence/proposition/motion+reservation: subjects specific & on-topic, at_stake = why-it-matters, agent views present-tense & party-blind
- [x] (generation run — validated by `verify metadata` + spot-check)

### Task 7: export-site integration

**Files:**
- Modify: `pipeline/aidag/export_site.py`
- Modify: `site/src/lib/data.ts`
- Modify: `tests/test_metadata.py`

- [x] factored hermetic helper `merge_case_metadata(payload, index_row, meta_rec)` in `export_site.py` (mutates both dicts, no I/O), called from `run()` after both dicts are built (payload written after merge so `meta` is included)
- [x] per-case JSON payload gets full display metadata (namespaced `meta`: `type`, `policy_area`, `subject{sv,en}`, `at_stake{sv,en}`, `subtopics`, `parties_involved`)
- [x] client index stays LEAN: adds only `policy_area` (code) + `type` + a lowercased `search` blob holding ONLY the NEW searchable text (`subject.sv + subject.en + subtopics`). It does NOT duplicate rubrik/rubrik_en/titel/bet (already on the row — the client ORs them in), which cut the index growth. NOT dual-lang subject fields, NOT `at_stake`. **Byte delta: 776KB → 1521KB uncompressed, but 324KB gzipped (what Cloudflare serves); the search feature adds ~174KB gzipped, the genuinely-new subject text that makes bundle votes searchable.**
- [x] added `CaseMeta` type + `meta?` to `CaseData`, `policy_areas` to `Meta`, and `search`/`policy_area`/`type`/`miss` to `IndexRow` in `data.ts`; added `policy_areas` labels to `meta.json`
- [x] wrote `TestMergeCaseMetadata`: full meta into payload; lean index row (search blob = subject+subtopics only, no display-field dup, no at_stake); clean fallback (no meta → no filter keys, no search)
- [x] `uv run aidag export-site --run-id full-v3` — regenerated `site/src/data` (2539 cases, all with `meta`); metadata-present `npm run build` green (7,725 pages)
- [x] unit tests pass (57)

### Task 8: CasePage — subject lead + at_stake + topic chip (bilingual)

**Files:**
- Modify: `site/src/pageviews/CasePage.astro`
- Modify: `site/src/i18n/ui.ts`

- [x] replaced the `utsknotis` `.summary` lead with `meta.subject` (`lead = subject || sammanfattning` fallback)
- [x] added a `.atstake` "what's at stake" block from `meta.at_stake`; **kept the existing `compact` meanings card** (verified still rendering, SV + EN)
- [x] added a `.chip.topic` policy-area chip near the header: `meta.policy_area` → localized label via `meta.policy_areas[code][lang]`
- [x] added sv+en `case.atStake` labels ("Vad som står på spel"/"What's at stake"); topic labels come from `meta.json` policy_areas
- [x] `npm run build` green (7,725 pages); grepped built `/fall/<id>/` (SV) + `/en/cases/<id>/` (EN): specific subject lead, at_stake block, topic chip all present
- [x] (UI e2e) bundle case C7E795A0 now leads with "Riksdagen röstar om att indexera skatterna…" (specific) instead of the generic "BNP-indexering…" blurb; `compact` card still renders

### Task 9: CaseBrowser — policy-area filter + subject-aware search

**Files:**
- Modify: `site/src/pageviews/CaseBrowser.astro`
- Modify: `site/src/i18n/ui.ts`

- [x] added a `policy_area` `<select>` filter — **option value = stable `policy_area` code**, label localized via `POLICY[code]`; populated from the index rows; wired into `apply()`
- [x] extended the client search: display fields (rubrik/rubrik_en/titel/bet, already on the row) OR the precomputed `search` blob (subject sv/en + subtopics) — delivers subject/subtopic search without duplicating display fields into the index
- [x] (dropped) no per-row dual-language subject snippet in the listing — subject shows on the case page only
- [x] added `browser.filter.allAreas` i18n labels; `npm run build` green; built `/fall/` has `id="area"` + "Alla ämnesområden"; index carries policy_area + search on all 2539 rows
- [x] (UI e2e) 15 policy_area codes populate the filter; search blob present on every row (subject/subtopic terms now match)

### Task 10: Open-data / downloads export

**Files:**
- Modify: `pipeline/aidag/export_site.py`
- Modify: `site/src/pageviews/AboutPage.astro` (Data section link)
- Modify: `tests/test_metadata.py`

- [x] emit `site/public/downloads/case-metadata.jsonl` during export — 2539 full records (det fields + subject + at_stake + subtopics + agent view + provenance)
- [x] linked it from the About page Data & code section (sv + en)
- [x] wrote `TestDownloadsExport`: well-formed JSONL, one row per exported case with metadata (skips cases without), rows include at_stake + agent
- [x] run tests — 57 pass; export produced 2539-row JSONL, all with at_stake+agent

### Task N-1: Verify acceptance criteria
- [x] all Overview goals present: reader lead (subject on CasePage) ✓, search/filter (topic filter + subject search) ✓, open-data export (case-metadata.jsonl) ✓, prepared+clean agent view (2539, verify green) ✓
- [x] `uv run pytest` green — **134 passed** (incl. `test_metadata.py`, 57)
- [x] `uv run aidag verify metadata` GREEN (2539 records, 0 leaky/misaligned) and `verify simulate --run-id full-v3` GREEN (6563 decisions, 0 hallucinated citations)
- [x] `npm run build` green (**7,725 pages**); spot-checked `/fall/` (topic filter) and `/fall/<id>/` + `/en/cases/<id>/` (subject lead, at_stake, chip, compact card)
- [x] `git diff --exit-code pipeline/aidag/promptgen.py` → **UNCHANGED** (decouple invariant holds)

### Task N: Publish + docs
- [ ] `export-site --run-id full-v3` → `npm run build` gate → commit `site/src/data data/results/metadata site/public/downloads` and the new pipeline/site code → push to `mooracle/airiksdagen`
- [ ] add a short `docs/orchestration-metadata.md` runbook (prepare → workflow(haiku) → ingest → verify)
- [ ] update the aidag memory (new metadata layer, Haiku synthesis, decoupled agent view) and CLAUDE/README if a pattern is worth recording
- [ ] move this plan to `docs/plans/completed/`

## Technical Details
- **Storage:** `data/results/metadata/cases.jsonl`, one JSON object per line keyed by `votering_id`; run-independent (describes the case, not any AI run) so it survives run changes — exactly like `data/results/translations/cases.jsonl`.
- **Record fields:** deterministic (`type`, `policy_area` code, `committee`, `n_motions`, `n_reservations`, `is_budget`, `parties_involved`); synthesis (`subject{sv,en}`, `at_stake{sv,en}` = *why-it-matters*, `subtopics[]`); de-leaked (`agent{subject,at_stake}`); provenance (`model`, `collected_at`).
- **Grounding:** synthesis agents read ONLY their request file. Human fields come from `display_src` (party-aware); the `agent.*` fields come from `agent_src` — the **scrubbed** input (`promptgen.scrub_text(..., "anonymous")`, parties dropped). Ingest re-attaches deterministic fields server-side so the model can't fabricate `type`/`policy_area`/`parties`.
- **De-leak guarantee vs tripwire:** the guarantee that `agent.*` is safe to wire into the prompt later is the *scrubbed input*, not the validator. The validator (`PARTY_TAG_RE`, full `PARTY_NAMES_RE`, `AUTHOR_RE`, `promptgen.FORBIDDEN_PATTERNS`, enumerated `OUTCOME_RE`) is a tested tripwire that catches regressions.
- **`at_stake` vs `compact`:** the deterministic `compact.py::compact_meanings` remains the Ja/Nej literal-meaning source (rendered as the meanings card); `at_stake` covers only *what's materially at stake* — no duplication/contradiction.
- **Model/cost:** Haiku 4.5, ~12 cases/agent → ~212 agents in one parallel workflow (below translate's 240 default; 16-wide concurrency runs it in minutes), negligible budget, preserves the Opus weekly window (full-v3 mid-run at 32%).
- **Index leanness:** the client-fetched `cases-index.json` gains only `policy_area` (code) + `type` (filter) + a single precomputed lowercased `search` blob (rubrik + rubrik_en + subject sv+en + subtopics). Full `subject`, `at_stake`, and the agent view stay in per-case JSON + the downloads file. Byte delta measured before commit.

## Post-Completion
*Informational — external/manual, no checkboxes.*

**Publish:** push triggers the Cloudflare Workers Build → live at airiksdagen.se. Ensure the active gh account is `alexsergeyev` before pushing (postnord account can't see the repo).

**Phase 2 (optional, separate):** fetch reservation full texts + motion contents from data.riksdagen.se (raises the 20-reference cap) and regenerate `at_stake` + the agent view with real Nej-side substance.

**Phase 3 (separate go/no-go):** quantify the motion-author leak (correlation between "party X's motion is in this vote" and "X votes Nej", and the agreement drop when blinded). If material, scrub motion-author tags in `promptgen.py`, wire `meta.agent.*` into the prompt, and re-run the simulation. This plan deliberately leaves `promptgen.py` untouched.
