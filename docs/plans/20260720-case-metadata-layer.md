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

- [ ] create `pipeline/aidag/metadata.py` with paths (`METADATA_DIR`, `cases_path()`), `_read_jsonl`, `load_metadata()` (dict keyed by `votering_id`) — mirroring `translate.py`
- [ ] add `UTSKOTT_AREA: dict[str, dict]` mapping every committee code (SfU, FöU, SkU, MjU, JuU, UbU, …) → `{"code": <stable slug>, "sv": …, "en": …}`. **`policy_area` stored as the stable `code`** (language-independent filter key); the sv/en labels live in this map and are surfaced to the site (via `meta.json` or an i18n map) — so the filter key never differs per build language (review nice-to-have)
- [ ] implement `extract_deterministic(case) -> dict`: `policy_area` (code), `committee`, `n_motions` (count motion refs), `n_reservations` (alternatives minus `utskottet`), `is_budget`, and `type` with **explicit precedence** — real betänkanden mix phrases (the `test_promptgen` golden case has both "antar regeringens förslag" *and* "avslår motion"), so classify in order: **budget** (utgiftsram/utgiftstak/`FiU1` rambeslut) → **proposition** ("antar regeringens förslag"/proposition ref present) → **motion** ("avslår motionerna") → **other**. `type` is best-effort (document as such)
- [ ] `parties_involved` (DISPLAY ONLY): PRIMARY source = reservation `source_partier` (structured, reliable); only fall back to scraping `forslag_text` author clauses via a reused `promptgen.AUTHOR_RE`. Flag as best-effort (a miss is cosmetic)
- [ ] write tests: `type` on a proposition, a motion-bundle, a budget case, AND the **mixed** case (proposition that also rejects a motion → must resolve to proposition by precedence) using real votering_ids; `policy_area` code for ≥3 committees; `parties_involved` from `source_partier`; `n_reservations`
- [ ] write tests for edge cases: empty/odd `forslag_text`, unknown committee → "ovrigt"/Other fallback
- [ ] run tests — must pass before Task 2

### Task 2: Grounded request units + `prepare()` / `status()`

**Files:**
- Modify: `pipeline/aidag/metadata.py`
- Modify: `tests/test_metadata.py`

- [ ] add `_case_unit(case)` with **two grounding blocks** so the party-blind agent fields can never be synthesized from party-aware text (review #1, load-bearing):
  - `display_src` (party-aware): `rubrik`, `utsknotis`, cleaned+truncated `forslag_text`, reservation texts, first ~15 `references` titles, `utskott`
  - `agent_src` (SCRUBBED): the same `forslag_text`/reservation texts run through `promptgen.scrub_text(text, "anonymous")`, with `parties_involved`/`source_partier`/author names DROPPED — this is the ONLY input the agent fields may use
  - never include post-decision `notis`
- [ ] add `INSTRUCTIONS`: from `display_src` produce human `subject{sv,en}` + `subtopics[]` + `at_stake{sv,en}` where **`at_stake` = "what is materially at stake / why this vote matters"** (NOT the literal Ja/Nej meanings — `compact.py::compact_meanings` already provides those deterministically; do not duplicate/contradict it, review #2). From `agent_src` ONLY produce `agent{subject,at_stake}` — party-blind, no outcome, pre-decision. State plainly: the guarantee is the scrubbed input; the validator (Task 3) is a tripwire, not the guarantee
- [ ] implement `_pending()` (cases in parquet not in `cases.jsonl`) and `prepare(batch_size, per_request=12)` writing request files + a `batch-NNN.json` manifest. **Metadata manifest OMITS `run_id` and the `kind` split** (run-independent, single-kind) — do NOT literally mirror translate's `MANIFEST_SCHEMA`/item shape (review #6). Default fan-out covers ALL pending
- [ ] implement `status()` (done/pending counts)
- [ ] write tests: `prepare` writes N request files + a manifest with matching `n_items` (no `run_id`/`kind`); `_pending` excludes already-ingested ids; **`agent_src` contains no party tag, no full party name, no author name, no `notis`** (golden — the scrub actually fired)
- [ ] run tests — must pass before Task 3

### Task 3: `ingest()` + validation (grounding + de-leak asserts) + `verify_metadata`

**Files:**
- Modify: `pipeline/aidag/metadata.py`
- Modify: `tests/test_metadata.py`

- [ ] define the de-leak checks by REUSING existing controls, not reinventing (review #3): import `promptgen.FORBIDDEN_PATTERNS` verbatim for beteckning/date; `PARTY_TAG_RE = \((S|M|SD|C|V|KD|MP|L)\)`; a `PARTY_NAMES_RE` built from `config.PARTIES[*]["name"]` (the eight full names — "Centerpartiet", "Vänsterpartiet", …); and an **enumerated, committed** `OUTCOME_RE` (sa ja/nej, biföll, avslog, godkändes, antogs, beslutade, föll, fick majoritet, röstade igenom, avslag på propositionen …)
- [ ] implement `validate_metadata(rec, unit)`: non-empty `subject.sv/.en`, `at_stake.sv/.en`; `subtopics` a list; `agent.subject`/`agent.at_stake` non-empty AND matching NONE of `PARTY_TAG_RE`, `PARTY_NAMES_RE`, `AUTHOR_RE`, `FORBIDDEN_PATTERNS`, `OUTCOME_RE` (raise otherwise). Comment: the validator is a tripwire; the real guarantee is the scrubbed `agent_src` input (Task 2)
- [ ] implement `ingest(input_path, model)`: read `{cases:[...]}`, validate each, merge with `extract_deterministic`, dedupe on `votering_id`, append to `cases.jsonl` with `model`+`collected_at` (idempotent)
- [ ] implement `verify_metadata()` generator (mirror `verify_translations`): yields (records align with source cases; every `agent.*` passes the de-leak checks; every ingested id exists in parquet)
- [ ] write tests: `validate_metadata` rejects an agent view leaking a party tag, a **full party name**, an author name, AND an outcome word; rejects empty fields; accepts a clean record
- [ ] write tests: `ingest` is idempotent (re-ingest adds 0), dedupes, merges deterministic fields; `verify_metadata` flags a planted leak
- [ ] run tests — must pass before Task 4

### Task 4: CLI commands + `verify metadata` stage

**Files:**
- Modify: `pipeline/aidag/cli.py`
- Modify: `tests/test_metadata.py`

- [ ] add `metadata-prepare` (`--batch-size`, `--per-request`), `metadata-ingest` (`INPUT_PATH`, `--model`), `metadata-status` — mirror the `translate-*` registration (cli.py ~176–208)
- [ ] wire `metadata` into the `stages` dict in `verify.py` (run-independent → gets into `verify all`/CI for free, unlike the special-cased `simulate`/`translate`) and update the `verify` help string (`votes|cases|kb|prompts|simulate|translate|metadata|site|all`)
- [ ] write a test invoking `verify metadata` returns clean on a tiny fixture jsonl and non-zero on a leaky one
- [ ] run tests — must pass before Task 5

### Task 5: Parallel batch workflow script (cheap model)

**Files:**
- Create: `scripts/metadata_batch_workflow.js`

- [ ] mirror `scripts/translate_batch_workflow.js`: `meta` block, `METADATA_UNITS_SCHEMA` (units: `{votering_id, subject{sv,en}, at_stake{sv,en}, subtopics[], agent{subject,at_stake}}`), and a `MANIFEST_SCHEMA` that **drops `run_id` and the `kind` enum** (metadata is run-independent, single-kind — review #6)
- [ ] fail-fast guard on missing/invalid `args.manifestPath` — check `args.manifestPath.includes('metadata')` (NOT `'translate'`); tolerate stringified args; loader agent reads the manifest
- [ ] `parallel()` fan-out one agent per request file with `model: "haiku"`, `phase: "Metadata"`, the units schema; agent prompt: read ONLY that file, follow its `instructions`, one output unit per input unit copying `votering_id`, answer only via structured output
- [ ] return `{ cases: <flattened units> }`
- [ ] verify (manual, in Task 6): run on a 5-case manifest → well-formed structured output

### Task 6: Generate metadata for ALL cases (parallel batch run)

**Files:**
- Modify: `data/results/metadata/cases.jsonl` (generated)

- [ ] `uv run aidag metadata-prepare --batch-size <cover-all>` → one manifest fanning out ~106 Haiku agents (24 cases each)
- [ ] `Workflow({ scriptPath: "scripts/metadata_batch_workflow.js", args: { manifestPath: "<abs>" } })`; extract task-output `json['result']` → temp file
- [ ] `uv run aidag metadata-ingest <file> --model claude-haiku-4-5` → validated append
- [ ] `uv run aidag verify metadata` — must be GREEN (0 leaky agent views, all aligned)
- [ ] re-`prepare`/re-run to backfill any cases whose agent died (checkpoint re-issues exactly the gaps) until `metadata-status` shows 0 pending
- [ ] spot-check ~6 varied cases (bundle, proposition, budget, reservation, migration, defence): subject on-topic, at_stake correct, agent view party-blind
- [ ] (no unit test — this is the generation run; validated by `verify metadata` + spot-check)

### Task 7: export-site integration

**Files:**
- Modify: `pipeline/aidag/export_site.py`
- Modify: `site/src/lib/data.ts`
- Modify: `tests/test_metadata.py`

- [ ] factor a small hermetic helper `merge_case_metadata(payload, index_row, meta_rec)` in `export_site.py` (so the merge is testable without running the monolithic `run()` — review nice-to-have) and call it from `run()`
- [ ] per-case JSON payload gets the full display metadata (namespaced `meta`: `type`, `policy_area` code, `subject{sv,en}`, `at_stake{sv,en}`, `subtopics`, `parties_involved`)
- [ ] client index (`cases-index.json`) stays LEAN (it's fetched for every row): add only `policy_area` (code) + `type` for filtering, and a **single precomputed lowercased `search` blob** = `rubrik + rubrik_en + subject.sv + subject.en + subtopics` for search — NOT the full `subject` in both languages per row, NOT `at_stake` (review #4). **Measure the byte delta** vs the current index and note it in the plan before committing
- [ ] add `CaseMeta` type + `meta?` to `CaseData`, and `search`/`policy_area`/`type` to the index-row type in `data.ts`
- [ ] write a test on `merge_case_metadata`: merges the metadata into payload + index; index carries the `search` blob (not dual-language subject); falls back cleanly when a case has no metadata record
- [ ] run tests + `uv run aidag export-site --run-id full-v3` — regenerates `site/src/data`
- [ ] run — tests must pass before Task 8

### Task 8: CasePage — subject lead + at_stake + topic chip (bilingual)

**Files:**
- Modify: `site/src/pageviews/CasePage.astro`
- Modify: `site/src/i18n/ui.ts`

- [ ] replace the `utsknotis` `.summary` lead with `meta.subject` (fall back to `utsknotis` when absent)
- [ ] add a **"what's at stake / why it matters"** block from `meta.at_stake` — do NOT restate Ja/Nej literal meanings; **keep the existing `compact` "meanings" card** (CasePage.astro:116-125) as the deterministic Ja/Nej source (review #2)
- [ ] add a policy-area/topic **chip** near the header: `meta.policy_area` (code) → localized label via the `UTSKOTT_AREA`/i18n map
- [ ] add sv+en i18n labels (`case.atStake` = "Vad som står på spel"/"What's at stake"; topic labels from the policy-area map)
- [ ] `cd site && npm run build` green; grep built `/fall/<id>/` for the subject + at_stake + chip
- [ ] (UI e2e substitute) verify a bundle case now leads with a specific subject, not the generic report blurb, and the `compact` card still renders

### Task 9: CaseBrowser — policy-area filter + subject-aware search

**Files:**
- Modify: `site/src/pageviews/CaseBrowser.astro`
- Modify: `site/src/i18n/ui.ts`

- [ ] add a `policy_area` `<select>` filter — **option value = the stable `policy_area` code**, label = localized (so the filter key is build-language-independent, review nice-to-have); populate from the index; wire into `apply()`
- [ ] extend the client search to match the index's precomputed lowercased **`search` blob** (already includes subject sv+en + subtopics) — delivers the earlier "search all about migration" keyword ask without bloating the index per-row
- [ ] (dropped) no per-row dual-language subject snippet in the listing — it would triple index weight (review #4) and fight the compact one-line rows; subject shows on the case page only
- [ ] add i18n labels; `npm run build` green; grep built `/fall/` for the new filter + that search matches the blob
- [ ] (UI e2e substitute) confirm filtering by "Migration" narrows the list and searching a subtopic term matches

### Task 10: Open-data / downloads export

**Files:**
- Modify: `pipeline/aidag/export_site.py`
- Modify: `site/src/pageviews/AboutPage.astro` (Data section link)
- Modify: `tests/test_metadata.py`

- [ ] emit `site/public/downloads/case-metadata.jsonl` (full records incl. `at_stake` + agent view) during export
- [ ] link it from the About page Data & code section
- [ ] write a test: the downloads file is well-formed JSONL with one row per exported case that has metadata
- [ ] run tests — must pass before Task N-1

### Task N-1: Verify acceptance criteria
- [ ] all Overview goals present: reader lead, search/filter, open-data export, prepared+clean agent view
- [ ] `uv run pytest` green (incl. `test_metadata.py`)
- [ ] `uv run aidag verify metadata` and `verify simulate` both GREEN
- [ ] `cd site && npm run build` green (7,7xx pages); spot-check `/fall/` and `/fall/<id>/`
- [ ] confirm `promptgen.py` unchanged (decouple invariant): `git diff --exit-code pipeline/aidag/promptgen.py`

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
