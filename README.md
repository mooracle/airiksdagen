# AI Riksdag — vad partierna skrev, och hur de röstade

**Live:** [airiksdagen.se](https://airiksdagen.se) · Built by [mooracle.io](https://mooracle.io) · Open research data & code (MIT)

Ett öppet forskningsprojekt som kopplar varje votering i sakfrågan i Sveriges riksdag under
mandatperioden 2022–2026 till vad partiernas egna dokument säger i frågan — och visar var
partiets röst gick åt andra hållet. För varje votering och parti (S, M, SD, C, V, KD, MP, L)
läser en AI-agent **enbart** partiets eget valmanifest och partiprogram, där varje dokument bara
är synligt från sitt antagandedatum, plus en tidsbunden lägesbild av landet. Agenten tar
ställning till det motförslaget kräver och citerar ordagrant de stycken ståndpunkten vilar på;
den röst ståndpunkten innebär härleds i kod. Blockdokument — Tidöavtalet och budgetmotionerna —
ingår medvetet inte: varje parti mäts mot sin egen plan.

Ståndpunkten jämförs med hur partiet faktiskt röstade och publiceras som en statisk webbplats
med källhänvisningar och en visualisering av kammarens 349 platser per ärende.
Källhänvisningarna går åt båda hållen: varje citerad rad i ett partidokument visar också vilka
voteringar som lutade sig mot just den raden, och om partiet sedan röstade med sin egen plan.

**Detta är ingen förutsägelse.** Agenten ombeds aldrig gissa hur partiet röstade och får aldrig
veta utfallet; ärendenummer, voterings-id och exakta datum tas bort ur prompten, maskinellt
upprätthållet av golden tests. Avvikelsen mellan partiets egen text och dess röst är
resultatet, inte ett fel att räkna bort. Projektet är partipolitiskt obundet; metodik, prompts,
kod och rådata är öppna.

> **English:** Links every substantive vote in the Swedish Riksdag 2022–2026 to what each party's own
> documents say about the question, and shows where the party voted the other way. For each vote
> and party, an AI agent reads only that party's election manifesto and party programme — each
> visible only from its adoption date — plus a point-in-time snapshot of the country. It takes a
> stance on what the counter-proposal demands and quotes the passages it rests on verbatim; the
> vote that stance implies is derived in code. Bloc documents (the Tidö agreement, budget
> motions) are deliberately excluded. **This is not vote prediction:** the agent is never asked
> how the party voted and never told the outcome (identifiers and exact dates are stripped from
> the prompt, enforced by golden tests). The divergence between a party's own text
> and its vote is the finding. Not affiliated with any party.

---

## Where to start

| I want to… | Do this |
|---|---|
| **Just see the results** | Open [airiksdagen.se](https://airiksdagen.se) |
| **Run the site locally** | `cd site && npm install && npm run dev` — reads committed JSON in `site/src/data/`, no Python needed |
| **Test the site** | `cd site && npm test` (`node --test`). Needs Node **≥ 22.18** for type stripping — above the 22.12 `.node-version` pins for Cloudflare's build image, which runs `npm ci && npm run build` only |
| **Explore the research pipeline** | `uv sync && uv run aidag --help` (Python 3.12+, [uv](https://docs.astral.sh/uv/)) |
| **Understand the method** | [`docs/methodology.sv.md`](docs/methodology.sv.md) / the site's `/om/` page (`/en/about/`), and [`docs/data-sources.md`](docs/data-sources.md) |
| **Reproduce a full run** | [`docs/orchestration-full-v4.md`](docs/orchestration-full-v4.md) (agent loop) + [`docs/deploy-cloudflare.md`](docs/deploy-cloudflare.md) (publish) |

## Repository layout

```
pipeline/aidag/     Python research pipeline — the `aidag` CLI (fetch → build → simulate → aggregate → export)
site/               Astro static site (this is what gets deployed); reads only site/src/data/
data/               Inputs and results (see table below)
docs/               Methodology, data sources, run/deploy runbooks
scripts/            Batch-workflow drivers (grouped_batch_workflow.js, run_batch.sh, …)
tests/              pytest suite (leakage/citation golden tests, coalition metric, …)
site/tests/         node --test suite for the site (block rendering, citation deep links)
wrangler.toml       Cloudflare Workers Build config (deploy)
```

## Where the data lives

The site is fully reproducible from committed data — the Cloudflare build runs **no Python** and
fetches nothing. Raw/intermediate artifacts are gitignored because they are re-fetchable from
public APIs (see [`docs/data-sources.md`](docs/data-sources.md)).

| Path | In git? | What it holds |
|---|---|---|
| `data/raw/` | gitignored | Raw API dumps from data.riksdagen.se (re-fetchable) |
| `data/processed/` | gitignored | `votes.parquet`, `cases.parquet`, `party_positions.parquet` |
| `data/interim/` | gitignored | Per-run agent system prompts + batch manifests |
| `data/corpus/` | **committed** | Party documents: valmanifest, partiprogram, budgetmotion, Tidöavtalet (layout below) |
| `data/kb/`, `data/worldstate/` | **committed** | Point-in-time country snapshots (economy + events, publication-vintage gated) |
| `data/results/simulations/<run>/` | **committed** | AI decisions — one JSONL per party (`S.jsonl`, `M.jsonl`, …) |
| `data/results/aggregates/<run>/` | **committed** | `summary.json`, `confusion.json`, `coalition.json`, `coverage.json`, `party_timeseries.json` |
| `data/results/anchors/<run>/` | **committed** | Every citation indexed back to the corpus block it quotes (one JSON per cited document) |
| `site/src/data/` | **committed** | Exported per-case JSON + indexes the site reads (regenerated by `aidag export-site`) |
| `site/public/data/`, `site/public/downloads/` | **committed** | Client-fetched case index, the per-document citation ref rows (8.3 MB, fetched only when a panel is opened) + downloadable decision dumps |

The published run is **`full-v4`** (prompt p6, 20,288 decisions — 2,539 voteringar × 8 parties,
less 24 on three points that reject a bundle of mutually contradictory motion yrkanden, which no
single stance can answer; `aidag undecidable-report` prints them with the source as evidence).
`full-v3` (p5) is kept in the
repo and still verifies. Everything is keyed on `run_id`. `mock-v1` is a synthetic run kept only
so tests and `npm run dev` work without the real data build.

### The document corpus

The 23 cited documents (8 valmanifest + 15 partiprogram) are extracted from cached PDFs into
**blocks with stable ids** — headings, paragraphs, bullets, labels — from font metrics and page
geometry, with no LLM in the path. The remaining 17 (16 budgetmotion + Tidöavtalet) keep their
original flat text.

```
data/corpus/
  pdf/<slug>.pdf            source bytes, archived so re-extraction never needs the network
  frozen/<slug>.txt         pre-extraction text (all 40) — what prompt versions below p6 are served
  blocks/<slug>.json        {slug, source, pages, dropped, blocks: [{id, role, page, size, bold, text}]}
  <slug>.txt                derived from the blocks; what the agents read and `verify simulate` checks
  known-unrecovered.json    the residual quotes no reading order recovers (12, all column artifacts)
```

`frozen/` is a prompt-version split rather than a fallback: p4/p5 decisions were generated from
those bytes, and each citation is checked as an exact substring of what the agent was actually
shown. Citation quotes are indexed back to blocks **by quote, not by offset**, so re-extracting a
document re-runs the locator instead of breaking every link.

## The pipeline

`uv run aidag <command>` — `--help` on any command for details.

**A. Build the inputs** (run once; re-fetchable, needs `data/raw`):

```
fetch-votes        bulk votering dumps → votes.parquet          (data.riksdagen.se)
fetch-cases        dokumentstatus per case                      (data.riksdagen.se)
build-cases        cases + actual party positions
fetch-corpus       manifestos, party programmes, budget motions, Tidöavtalet
extract-corpus     re-extract the 23 cited documents into blocks + derived text (offline)
build-kb           monthly point-in-time country snapshots      (Riksbanken, SCB, Wikipedia)
build-worldstate   per-date economy + events blocks (point-in-time)
```

**B. Run the agents and publish** (the full-v4 loop, see `docs/orchestration-full-v4.md`):

```
agent-prepare      emit the next subagent batch manifest (checkpoint-aware)
   → run scripts/grouped_batch_workflow.js  (Opus agents via Claude Code subscription — no API key)
agent-ingest       ingest a workflow batch result into the results layout
citation-audit     snapshot the citation record's shape; diff it after any pass that rewrites it
migrate-quotes     move committed quotes onto a re-extracted corpus (before repair, never after)
repair-citations   align paraphrased quotes to the verbatim source span (+ block-list / weak-tier)
verify simulate    integrity gate — no hallucinated or out-of-context citations
build-anchors      index every citation back to the block it quotes (fails on an unlocated quote)
navigation-report  which citations landed on a heading or topic label rather than a promise
aggregate          agreement stats, confusion matrices, coalition-vs-programme metric
export-site        write per-case JSON + indexes → site/src/data/
translate-*        English translations (checkpoint-aware); compare-runs, agent-status
recall-probe-*     contamination: does the model recognise the vote from month + committee + title?
                   (sample → run → report; see docs/methodology.en.md)
```

> Decisions are produced by Claude Code subagents on a Claude subscription (no Anthropic API key
> required). The legacy `select-pilot` / `simulate` / `collect` commands drive the Anthropic Batch
> API path and are kept for reference; the published `full-v4` run uses the subagent workflow.

**Publish a refresh:** `aggregate` → `export-site` → `cd site && npm run build` (gate) → commit
`site/src/data site/public/data data/results` → `git push` (Cloudflare rebuilds the site).
`site/public/data` is the browser-fetched half (citation anchors); leaving it out ships a site
whose citation panels 404. See [`docs/deploy-cloudflare.md`](docs/deploy-cloudflare.md).

## Data sources & attribution

- Voteringar och dokument: **Källa: Sveriges riksdag** ([data.riksdagen.se](https://data.riksdagen.se))
- Valmanifest, partiprogram: [SND Vivill](https://snd.se/sv/vivill) (Public Domain Mark)
- Makrodata: Sveriges riksbank, SCB. Händelser: Wikipedia (CC BY-SA 4.0)

Every endpoint and license is listed in [`docs/data-sources.md`](docs/data-sources.md). Code is MIT-licensed.

---

Built by **[mooracle.io](https://mooracle.io)** · Live at **[airiksdagen.se](https://airiksdagen.se)**
