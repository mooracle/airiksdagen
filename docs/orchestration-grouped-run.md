# Unified grouped run (full-v2) — operator runbook

The single document needed to execute the **complete, uniform simulation**:
all 2,539 voteringar × 8 parties (20,312 decisions) + 2,539 memorization
probes, every decision produced the SAME way — grouped Sonnet agents, 8
same-party/same-month cases per agent. Supersedes the per-case protocol in
`orchestration-full-run.md` for execution; run from an interactive Claude
Code session (Opus orchestrator), no API key needed.

## Run identity — fixed for the whole run

| | |
|---|---|
| run_id | `full-v2` |
| prompt | **p4** (unchanged from full-v1; do not modify prompts mid-run) |
| arm | `anonymous` |
| execution | grouped, `--group 8` (one Sonnet agent per ≤8 same-party/month cases) |
| vote/probe model | `claude-sonnet-4-6` |
| batch_id at ingest | `claude-code-workflow-grouped8` |
| workflow script | `scripts/grouped_batch_workflow.js` |

## Why grouped, and why a fresh run_id

Validated 2026-07-03 on 256 decisions run three ways (all committed):

| runs compared | vote match |
|---|---|
| per-case vs per-case re-run (intrinsic sampling variance) | 92.2% |
| per-case vs grouped | 90.6% |
| per-case re-run vs grouped | 91.4% |

Grouping's vote differences are **within the model's intrinsic sampling
variance** (7.8% self-flip rate); agreement with real votes is identical
(84.4 / 84.8 / 85.7%); citation verification clean in all three. Cost is
**~12k tokens/decision vs ~42k per-case (3.5×)**. One measured design
effect: agents flag `omvarld.paverkar` ~4× more often when they see a
month's cases together — accepted as the metric's definition for full-v2
and disclosed in the methodology page.

`full-v1` (256 per-case decisions), `repro-pilot-v1` and `grouped-pilot-v1`
are **kept, never extended** — they are the published robustness study.
`full-v2` starts from zero so every one of its decisions has identical
provenance. Case-text translations in `data/results/translations/cases.jsonl`
are run-independent and carry over; decision translations do NOT (new
motiveringar) — full-v2 gets its own.

## Prerequisites (verify once per session)

```sh
uv run aidag verify cases       # case data intact
uv run aidag verify worldstate  # per-date worldstate built (build-worldstate if missing)
uv run pytest -q                # prompt-leakage goldens green
uv run aidag agent-status --run-id full-v2   # where the run stands
```

## The batch loop

Repeat until `agent-status --run-id full-v2` reports nothing pending:

```text
1. PREPARE   uv run aidag agent-prepare --run-id full-v2 --group 8 --batch-size 480
             → prints data/interim/agentrun/full-v2/batches/batch-NNN.json
             (~80 group agents; probes fill batch room automatically once all
              decisions are collected — no separate probe phase)

2. RUN       Workflow tool:
               { scriptPath: "scripts/grouped_batch_workflow.js",
                 args: { manifestPath: "<absolute path from step 1>" } }
             ⚠ args must be a JSON OBJECT, not a string (the script parses
             string args as a fallback, but don't rely on it).
             ⚠ Confirm the first log line: "batch loaded: NN groups
             (480 decisions), 0 probes (run full-v2)" — the script fail-fasts
             on missing args and on any manifest-transcription mismatch, so a
             clean "batch loaded" line means it is safe to walk away.
             Runs in background; ~80 groups ≈ 60–90 min, ~6M subagent tokens.
             Do NOT prepare/regenerate manifests while a workflow is running.
             Never run two workflows at once.

3. INGEST    write the workflow result JSON (the task output file's "result"
             field) to a temp file, then:
               uv run aidag agent-ingest --run-id full-v2 --input <file> \
                   --model claude-sonnet-4-6 --batch-id claude-code-workflow-grouped8
               uv run aidag repair-citations --run-id full-v2

4. VERIFY    uv run aidag verify simulate --run-id full-v2
             — must be green before the next batch (dupes, verbatim quotes,
             in-context documents). Unverifiable citations after repair = STOP
             and investigate. Repaired-quote share above ~5% = tighten the
             verbatim instruction before continuing (grouped baseline ~3%).

5. CHECKPOINT
             git add data/results && git commit -m "full-v2 batch NNN: <n> decisions"
             uv run aidag agent-status --run-id full-v2

6. TRANSLATE (can lag several batches behind; ALWAYS after repair-citations)
               uv run aidag translate-prepare --run-id full-v2 --batch-size 240
               Workflow { scriptPath: "scripts/translate_batch_workflow.js",
                          args: { manifestPath: "<printed path>" } }
               uv run aidag translate-ingest --run-id full-v2 --input <file>
               uv run aidag verify translate --run-id full-v2
               git add data/results/translations && git commit
             (case texts are packed first and are run-independent; use
              --kind cases / --kind decisions to run one side only)

7. LIMIT CHECK (below) → loop to 1 or stop cleanly.
```

Batches walk chronologically (2022-10 → 2026-06), so a partial run is always
a coherent "first N months" dataset that can be published at any checkpoint.

## Respecting limits

Subscription usage comes in ~5-hour windows plus a weekly cap.

- **One workflow at a time**, always.
- **Batch size 480** (~80 groups ≈ ~6M tokens ≈ 60–90 min) is a comfortable
  unit; expect 2–3 batches per usage window. Drop to 240 if returns thin out.
- **Detect limit pressure** from the result counts: `sims.length` well below
  the manifest's decision count (whole groups returning null late in the
  batch) means the window is likely exhausted — ingest what arrived, commit,
  stop for this window. A missing group = its ~8 decisions simply stay
  pending and are re-issued by the next prepare (measured once: 12/256).
- **Stopping is always safe**, including killing a running workflow: nothing
  is recorded until ingest, ingest is idempotent (dedupe on full custom_id),
  and state is derived from the committed JSONL — "resume" is just running
  the loop again. Nothing needs handover between sessions except this file.

## Scale and schedule

Measured (grouped-pilot-v1): ~12k subagent tokens per decision, ~8k per probe.

| | count | est. tokens | batches (480) |
|---|---|---|---|
| Decisions | 20,312 | ~245M | ~43 |
| Probes | 2,539 | ~20M | ~6 |
| Translations (cases + decisions + loaders) | ~2,300 agents | ~30M | ~10 (240) |
| **Total** | | **~295M** | **~59** |

At 2–3 batches per window and 2–3 windows per day: **roughly 7–10 calendar
days**, vs 3+ weeks for the per-case design. If faster is needed, the same
prompts run per-case on the paid Batch API (`aidag simulate`, Sonnet
~$400–600, 1–2 days) — but that reintroduces the mixed-design question;
decide before starting, not mid-run.

## Verification gates

Per batch (step 4): schema validity, custom_id uniqueness, verbatim quotes,
citations only to in-context documents (tidoavtalet gated by party+date).
Ingest additionally rejects ids not in the case universe and cids whose
party/vid disagree with the manifest.

Every ~10 batches and at the end:

```sh
uv run aidag aggregate --run-id full-v2
uv run aidag export-site --run-id full-v2
cd site && npx astro build && cd ..
uv run pytest -q
uv run aidag verify translate --run-id full-v2
```

Sanity expectations (grouped design):
- M/KD/L agreement high, κ near 0; V/MP/S lower agreement, meaningful κ.
- Avstår recall is the weak spot — track per batch.
- `omvarld.paverkar` baseline is HIGHER under grouping (~6% vs ~2% per-case
  on the validation set). Watch for *drift over time* and crisis-month
  spikes, not the absolute level; a flat-high rate across quiet months =
  stop and review.
- Probe recall low overall, spiking only for famous votes; >~20% overall
  means contamination needs more prominent reporting.

## Publishing checkpoints

Any time after step 5:

```sh
uv run aidag aggregate --run-id full-v2
uv run aidag export-site --run-id full-v2
# commit, set RUN_ID=full-v2 in .github/workflows/deploy.yml, push
```

Pages degrade gracefully: cases without decisions show real votes only;
untranslated content falls back to Swedish with a notice.

Before first publication, add to the methodology pages: the grouped
execution design, the three-run validation (92.2 / 90.6 / 91.4), and the
omvärld-rate sensitivity note.

## Failure modes seen so far (all handled, know the shape)

| symptom | meaning | action |
|---|---|---|
| workflow throws `args.manifestPath missing` immediately | args passed as string or lost | relaunch with args as JSON object |
| loader exceeds output tokens / returns null | manifest too big or agent died | manifests are compact now; just relaunch |
| "manifest failed integrity check" | loader transcription corrupted | relaunch (costs one loader agent) |
| `WARNING: N decisions with unknown cids` | agent hallucinated a cid | ignore — real ids stay pending |
| sims count < manifest count | group agents died / window limit | ingest, commit, stop this window |
| ingest "skipped ...: not in cases" | corrupted id survived to ingest | ignore (stays pending); investigate if frequent |

## File map

| Path | What |
|---|---|
| `scripts/grouped_batch_workflow.js` | THE workflow for full-v2 (groups + probes) |
| `scripts/translate_batch_workflow.js` | translation batches |
| `pipeline/aidag/agent_run.py` | prepare (--group/--mirror-run) / status |
| `pipeline/aidag/ingest_agent_run.py` | agent-ingest (idempotent, validated, --batch-id) |
| `pipeline/aidag/compare_runs.py` | run-vs-run comparison (the validation tool) |
| `data/interim/agentrun/full-v2/` | request files + manifests (gitignored, regenerable) |
| `data/results/simulations/full-v2/` | the scientific record (committed) |
| `data/results/translations/` | cases.jsonl (global) + full-v2/decisions.jsonl |
| `data/results/simulations/{full-v1,repro-pilot-v1,grouped-pilot-v1}/` | frozen validation study — never extend |
| `docs/orchestration-full-run.md` | superseded per-case protocol (kept for the record) |
