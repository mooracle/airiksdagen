# AI Riksdag — working notes for Claude

Research pipeline (Python, local-only) + a static Astro site (`site/`) that builds
from committed data in `site/src/data/`. The site deploys to Cloudflare from the
repo root via `wrangler.toml`; the Python pipeline never runs in the cloud build.

- Python: `uv run aidag <command>` (never bare `pytest` — the venv is uv-managed;
  `uv run pytest tests -q`).
- Site: `cd site && npm run build`. Preview with `npx astro preview --port 4322`.
  Keep this script as plain `astro build`. The Cloudflare deploy runs it through
  `wrangler`'s custom build (`cd site && npm ci && npm run build`) under **dash**,
  so anything bash-only fails the deploy with `Illegal option -o pipefail` and
  exit 2 — `/bin/sh` on macOS is bash in posix mode and will not reproduce it.
  Astro logs one route line per page (~7,700 lines) and offers no config knob to
  stop it: `logLevel` is absent from its schema and comes only from the
  `--verbose`/`--silent` flags. If the log ever needs quieting, filter at the
  call site (`npm run build | sed '/├─/d'`) rather than inside the script.
- `PROMPT_VERSION` in `pipeline/aidag/config.py` is the current prompt version and
  is **`p6`** (the policy-first stance schema the live run uses). It is part of the
  cid, so a default that lags the run makes `agent-status` report `0/20312 done`
  against a full results file and `agent-prepare` emit manifests for the wrong
  schema. Bump it with the run, and pass `--prompt-version p5` when working on
  full-v3 or earlier. Tests that assert version-specific *rendering* must pin their
  version rather than ride the default (see `P5` in `tests/test_promptgen.py`); the
  leakage guards deliberately ride the default so they always cover the live one.

---

## The document corpus — extraction, `frozen/`, anchors

23 of the 40 corpus documents (8 valmanifest + 15 partiprogram — the cited ones) are
extracted from cached PDFs into **blocks with stable ids**. The other 17 (16
budgetmotion + Tidöavtalet) keep their original flat text *and* their original
rendering path.

```
data/corpus/
  pdf/<slug>.pdf            source bytes, committed (23 files, 24 MB) — extraction never fetches
  frozen/<slug>.txt         pre-extraction text, all 40 — serves prompt_version < p6
  blocks/<slug>.json        {slug, source, pages, dropped, blocks: [{id, role, page,
                            size, bold, text}]} — 23 files, 9,409 blocks. `source` is
                            `pdf` except valmanifest-2022-c (`text`, no PDF text layer);
                            `dropped` is the running-header audit trail
  <slug>.txt                derived FROM the blocks; what agents read and verify checks
  known-unrecovered.json    12 quotes / 119 citations the geometry cannot recover
```

Roles: `h1 h2 h3 para bullet label toc caption unreadable`.

**`frozen/` is a version split, not a fallback.** `corpus._text()` reads `frozen/`
whenever `prompt_version < "p6"` — full-v2 (p4) and full-v3 (p5) were generated from
those bytes, and `verify simulate` checks every citation is an exact substring of what
the agent was shown, so a re-extracted file served to an old run fails citations that
were never wrong. There is deliberately **no** "use `frozen/` if it exists" rule: all 40
are frozen, including the 17 nothing touched, so the version test alone decides the
directory and cannot be wrong about which files moved.

**Extraction is offline, and no LLM is in this path** — a model that paraphrases a party
programme is a credibility failure for this project, so structure comes from font
metrics and geometry. `docx.py` does the work (`read_lines` → `strip_running` →
`detect_columns` → `split_blocks` → `style_clusters`/`assign_roles`) via PyMuPDF;
`extract_corpus.py` drives it and derives the `.txt` from the blocks so text and
structure cannot drift. `fetch_corpus.source_pdf_bytes()` raises `FileNotFoundError`
rather than downloading, so a re-extraction can never silently pick up a newer edition
than the corpus was built from.

**The passes are ordered, and re-running one alone is wrong:**

```sh
uv run aidag citation-audit   --run-id full-v4 --out data/interim/audit/before.json
uv run aidag extract-corpus --force              # blocks + derived .txt (23 documents)
uv run aidag migrate-quotes   --run-id full-v4   # committed quotes → new bytes (citat_migrerat)
uv run aidag repair-citations --run-id full-v4   # model paraphrases        (citat_korrigerat)
uv run aidag citation-audit   --run-id full-v4 \
    --baseline data/interim/audit/before.json --check-translations
uv run aidag build-anchors    --run-id full-v4   # quote → block index
uv run aidag verify simulate  --run-id full-v4   # and --run-id full-v3 — both must be green
uv run aidag export-site      --run-id full-v4
```

- `extract-corpus` **skips any slug whose block file already exists** — on this repo all
  23 do, so without `--force` the whole step prints "blocks exist, skipping" and the
  passes below then run against unchanged blocks. `fetch-corpus` will not rewrite these
  23 `.txt` files at all, `--force` included: they are derived from the blocks, and only
  re-extraction regenerates them.
- `migrate-quotes` records *the corpus changed*; `repair-citations` records *the model
  paraphrased*. Running them the other way round attributes an extraction fix to the
  agent. Both leave the original in a sidecar (`quote_fore_migrering`,
  `quote_ej_verifierad`) and never blank a quote silently.
- `build-anchors` is keyed on the **quote** — offsets are derived at build time, so
  re-extraction re-runs the locator instead of breaking links. It **fails** on any quote
  that neither resolves nor is blank/`citat_ej_migrerat`, before writing anything.
- `citation-audit` exists because `repair-citations` can *remove* citations while the
  English translations pair **positionally** (`export_site.py` → `CasePage.astro`): a
  decision whose citation list changes length renders the wrong English quote against
  the wrong Swedish one, with nothing raised anywhere. Snapshot before, diff after.
- `known-unrecovered.json` is consulted by both `migrate-quotes` and `repair.py`: a
  listed quote is never fuzzy-rewritten. Offered to `best_span` all 12 score > 0.75 —
  against the *neighbouring column*. `tests/test_docx.py::TestKnownUnrecovered` fails if
  the list grows or if a listed quote starts resolving.

**Site side.** `export-site` also writes `site/src/data/corpus/{blocks,anchors}/`
(inline counts, tiers and spans) and `site/public/data/anchors/` (the per-vote ref rows,
fetched only when a panel is opened — 8.3 MB that never loads with the page).
`doctext.renderDoc()` renders from blocks when the slug has them and falls back to
`formatCorpusDoc()` for the other 17. `groupBlocks()` rejoins paragraphs the extraction
cut: presentation only, and load-bearing — one `<p>` per block breaks 56 `#:~:text=`
deep links, because the browser's fragment matcher will not cross a block boundary.

**The site has its own test suite**: `cd site && npm test` (`node --test`, needs Node ≥
22.18 for type stripping). It covers the block renderer and sweeps every anchor's deep
link — guarantees that live in TypeScript and Astro, where `uv run pytest` cannot reach.
It is **not** wired into the Cloudflare build, which runs `npm ci && npm run build` only.

`aidag navigation-report --run-id full-v4` reports the citations that landed on a
heading or topic label rather than on a promise — **16 blocks, 178 votes** (180
vote-line pairs, which is the number the site shows per block). The finding is written
up in `docs/topic-label-citations.md`. The report prints three scopes because they are
three different numbers: `label_toc` (the `label`/`toc` roles alone) reads **1,165**
vote-line pairs and printing *that* would be a false claim about two parties' pledge
lists; `role_only` (every navigational role, headings included) reads **1,433**;
`navigational` — role *and* the text reading as a label, which is what the site marks —
is the 180. `site/tests/corpus.test.mjs` re-measures the last of these in TypeScript
against the committed export, so the duplicated rule cannot drift in one language only.

---

## Running the English translations

The English site shows translated case texts **and** translated AI reasoning.
Missing translations fall back to Swedish silently, so a partial run looks like a
half-Swedish page rather than an error.

State as of 2026-08-15: **case texts 2539/2539**, **AI decisions 20312/20312** —
both passes are complete, both on `claude-haiku-4-5`, so `translate-prepare` emits
nothing today. Work appears again only when new decisions land or when units are
deleted from `decisions.jsonl` to re-run them. Always read it off the tool rather
than from this page:

```sh
uv run aidag translate-status --run-id full-v4
```

### Use Haiku

Translation is the one place in this project where Haiku is the right default,
not a downgrade:

- The completed case-text pass ran on `claude-haiku-4-5` — mixing models across
  the corpus is the thing worth avoiding, so match it.
- `INSTRUCTIONS` in `pipeline/aidag/translate.py` carries an explicit terminology
  glossary, so the consistency that would otherwise depend on model strength is
  pinned in the prompt instead.
- ~3x cheaper than Sonnet (~$1.5 vs ~$4.5 per 1,000 decisions in shadow API
  terms — so ~$31 vs ~$91 for a full 20,312-decision pass; the real cost is
  Claude Code usage, since these run as subagents with no API key).

The workflow's own default is still `sonnet` so an unchanged launch behaves as it
always did — **pass `model: "haiku"` explicitly.**

### The three steps

**1. Prepare** — checkpoint-aware, emits only what is still pending. Run this
*after* `repair-citations`, never before: quote translations must be made from
the repaired verbatim Swedish.

```sh
uv run aidag translate-prepare --run-id full-v4 --kind decisions --batch-size 240
```

Writes `data/interim/translate/full-v4/batches/batch-NNN.json` plus one
self-contained request file per agent under `reqs/`. At
`DECISIONS_PER_REQUEST = 40` the agent count is `ceil(pending / 40)` — **0** today,
and **508** for a full 20,312-decision re-translation (`--batch-size 240` caps
groups per manifest, not units).

Note that 240 cap: 508 agents do not fit one manifest, so a full pass makes
`translate-prepare` emit several. Run each in turn — one `Workflow` call per
`batch-NNN.json`.

**2. Run the workflow** — up to 240 agents per manifest against a concurrency cap
of `min(16, cores-2)`, so roughly **2–4 h** wall clock per full manifest.

```
Workflow({
  scriptPath: "scripts/translate_batch_workflow.js",
  args: {
    manifestPath: "/abs/path/data/interim/translate/full-v4/batches/batch-001.json",
    model: "haiku",
  },
})
```

`args.model` accepts `haiku|sonnet|opus`. The manifest-loader agent stays on
Sonnet regardless — it has to emit every item as exact structured output, and that
reliability is worth one agent's cost.

**3. Ingest**, then rebuild the site. Pass the model you actually ran on; it is
recorded per row.

```sh
uv run aidag translate-ingest --run-id full-v4 --input <workflow-result.json> \
    --model claude-haiku-4-5
uv run aidag translate-status --run-id full-v4     # expect N/N, 0 pending
```

### Checkpointing — what a failure costs

Done means "id present in `data/results/translations/full-v4/decisions.jsonl`".
An agent that dies returns `null`, its 40 units stay pending, and the next
`translate-prepare` re-issues exactly those. Nothing is corrupted by stopping a
run mid-flight; you lose only in-flight work.

The corollary: **do not trust the workflow's own completion count.** Re-run
`translate-status` and confirm 0 pending — a dead agent is silent.

### After ingesting, verify the glossary held

The glossary exists because hundreds of independent agents share no context, and their
prose has to agree with the site's English UI labels. Spot-check the output:

| Swedish | must render as | UI label it has to match |
|---|---|---|
| `planen` | the plan | `case.planVsReal` "Plan vs actual vote" |
| `motförslaget` | the counter-proposal | `stance.short.stodjer` "supports counter-proposal" |
| `partiprogram` | party programme | `docLabel.partiprogram` |
| `uttryckligen` / `åtagande` | explicitly / commitment | `tier.explicit` "explicit commitment" |

If a term drifts, fix `GLOSSARY` in `pipeline/aidag/translate.py` and re-run only
the affected units (delete their ids from `decisions.jsonl`, then re-prepare).

### Why not one agent translating many groups in sequence

Tempting (shared context → consistent terminology, fewer agents) but measurably
worse. The agent's own prior source *and* output accumulate in its context and are
re-read on every later turn. Even at the 0.1x cache-read rate that overhead passes
the ~500-token preamble it saves within a few turns: **~12% more expensive at 100
units/agent, ~27% at 200.** The glossary gives the same consistency across the
whole corpus rather than only within one agent, for about $0.50.

Note also that output is ~76% of the bill and is identical under every batching
scheme — there is very little to win on the input side.
