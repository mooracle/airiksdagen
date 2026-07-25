# AI Riksdag — working notes for Claude

Research pipeline (Python, local-only) + a static Astro site (`site/`) that builds
from committed data in `site/src/data/`. The site deploys to Cloudflare from the
repo root via `wrangler.toml`; the Python pipeline never runs in the cloud build.

- Python: `uv run aidag <command>` (never bare `pytest` — the venv is uv-managed;
  `uv run pytest tests -q`).
- Site: `cd site && npm run build`. Preview with `npx astro preview --port 4322`.

---

## Running the English translations

The English site shows translated case texts **and** translated AI reasoning.
Missing translations fall back to Swedish silently, so a partial run looks like a
half-Swedish page rather than an error.

State as of the last check: **case texts 2539/2539 done**, **AI decisions
0/5400** — the decision pass has never been run. Check before starting:

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
- ~3x cheaper than Sonnet (~$8 vs ~$24 in shadow API terms for the 5,400
  decisions; the real cost is Claude Code usage, since these run as subagents
  with no API key).

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
`DECISIONS_PER_REQUEST = 40` the 5,400 decisions pack into **135 agents**, which
fits in a single batch (`--batch-size 240` caps groups per manifest, not units).

**2. Run the workflow** — 135 agents against a concurrency cap of
`min(16, cores-2)`, so roughly **1–2.5 h** wall clock.

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
Sonnet regardless — it has to emit 135 items as exact structured output, and that
reliability is worth one agent's cost.

**3. Ingest**, then rebuild the site. Pass the model you actually ran on; it is
recorded per row.

```sh
uv run aidag translate-ingest --run-id full-v4 --input <workflow-result.json> \
    --model claude-haiku-4-5
uv run aidag translate-status --run-id full-v4     # expect 5400/5400
```

### Checkpointing — what a failure costs

Done means "id present in `data/results/translations/full-v4/decisions.jsonl`".
An agent that dies returns `null`, its 40 units stay pending, and the next
`translate-prepare` re-issues exactly those. Nothing is corrupted by stopping a
run mid-flight; you lose only in-flight work.

The corollary: **do not trust the workflow's own completion count.** Re-run
`translate-status` and confirm 5400/5400 — a dead agent is silent.

### After ingesting, verify the glossary held

The glossary exists because 135 independent agents share no context, and their
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
