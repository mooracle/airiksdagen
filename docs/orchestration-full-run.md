# Full-run orchestration: Opus orchestrator + Sonnet vote agents

Operational runbook for simulating **all 2,539 voteringar × 8 parties (20,312
decisions) + 2,539 memorization probes** for 2022–2026, using Claude Code
subagents on a subscription — no API key. Written to be executed by an **Opus
orchestrator session** (paste this file or point the session at it); every
vote/probe agent runs on **Sonnet**.

Validated end-to-end by `agent-pilot-v1` (91 agents, 100% collection rate,
all citations substring-verified).

## Roles

| Role | Model | Responsibility |
|---|---|---|
| Orchestrator | Opus (the interactive Claude Code session) | prepare batches, launch workflows, ingest, verify, decide continue/stop |
| Vote agent | Sonnet (`claude-sonnet-4-6`, one per case × party) | read role file + case file → structured decision: `rost`, short Swedish `motivering` (2–4 sentences), verbatim `citations` into the party's own documents, `coverage`, `flags` |
| Probe agent | Sonnet (one per case) | cold recall of the real outcome (contamination measurement); no party documents |

Prompt version is **p2** (short motivering). Do not mix runs across prompt
versions in one `run_id`.

## Checkpoint model — where the state lives

There is **no separate checkpoint file**. State is derived, which makes
resumption trivial and crash-proof:

- **Done** = custom_ids already in `data/results/simulations/{run_id}/*.jsonl`
  and votering_ids in `data/results/probes/{run_id}/probe.jsonl` (both are
  append-only, committed to git after each batch).
- **Pending** = (all cases × parties) − done. `aidag agent-prepare` always
  emits the *next* batch from pending, chronologically ordered.
- Agents that fail, get skipped, or die when a usage limit hits simply return
  nothing → their ids stay pending → the next `agent-prepare` re-issues them.

So "continue from last checkpoint" is literally: run the loop again.

## The batch loop (orchestrator protocol)

Repeat until `agent-status` reports nothing pending:

```text
1. PREPARE   uv run aidag agent-prepare --run-id full-v1 --batch-size 240
             → prints data/interim/agentrun/full-v1/batches/batch-NNN.json
2. RUN       Workflow tool:
               { scriptPath: "scripts/agent_batch_workflow.js",
                 args: { manifestPath: "<absolute path from step 1>" } }
             (runs in background; ~250 Sonnet agents ≈ 30–50 min; the workflow
              returns { run_id, sims, probes } with only successful results)
3. INGEST    write the workflow result JSON to a temp file, then:
               uv run aidag agent-ingest --run-id full-v1 --input <file> \
                   --model claude-sonnet-4-6
             (idempotent: dedupes on custom_id, safe to re-ingest)
4. VERIFY    uv run aidag verify simulate --run-id full-v1
             — must be green (no dupes, no hallucinated citations) before the
             next batch. A nonzero hallucination count = STOP and investigate.
5. CHECKPOINT
             git add data/results && git commit -m "full-v1 batch NNN: <n> decisions"
             uv run aidag agent-status --run-id full-v1   # progress report
6. LIMIT CHECK (see below) → either loop to 1 or stop cleanly.
```

Batches walk **chronologically** (2022-10 → 2026-06), so a partially complete
run is still a coherent "first N months" dataset that can be aggregated,
exported and published at any point.

Probes automatically fill batch room once all decisions are collected — no
separate probe phase to manage.

## Respecting limits

Subscription usage comes in ~5-hour windows plus a weekly cap. Rules for the
orchestrator:

- **One batch at a time.** Never launch a second workflow while one runs.
- **Batch size 240** ≈ 7–8M subagent tokens ≈ a comfortable fraction of one
  window. Reduce to 120 if collection rates drop.
- **Detect limit pressure** from the workflow result: if `sims.length` is well
  below the manifest size (agents returning null late in the batch), the
  window is likely exhausted. Ingest what arrived, commit, and **stop for this
  window** — do not immediately retry; the pending ids are safe.
- **Stopping is always safe** at any point in the loop, including mid-workflow
  kills: nothing is recorded until ingest, and ingest is idempotent.
- On the next session (same day or weeks later): `aidag agent-status
  --run-id full-v1`, then resume at step 1. Nothing else to restore.

## Scale and schedule estimate

Measured baseline (pilot): ~32k subagent tokens per decision, ~8k per probe.

| | count | est. tokens | batches (240) |
|---|---|---|---|
| Decisions | 20,312 | ~650M | ~85 |
| Probes | 2,539 | ~20M | ~11 |
| **Total** | 22,851 | **~670M** | **~96** |

At 1–3 batches per usage window this is a **multi-week background effort** on
a subscription. If that is too slow, the same prompts run on the paid Batch
API via `aidag simulate` (Opus ~$900–1,300 or Sonnet ~$400–600, done in ~1–2
days) — the results layout is identical, so the two paths can even be mixed
across different `run_id`s and compared.

## Verification gates

Per batch (step 4): schema validity, custom_id uniqueness, citation quotes are
verbatim substrings of the cited document (whitespace-normalized).

Additionally after every ~10 batches and at the end:

```sh
uv run aidag aggregate --run-id full-v1        # agreement, kappa, Avstår recall
uv run aidag export-site --run-id full-v1      # refresh site data
cd site && npx astro build                     # must build clean
uv run pytest -q                               # prompt-leakage goldens
```

Sanity expectations: M/KD/L agreement high with κ near 0 (they vote for their
own government's proposals); V/MP/S agreement lower with meaningful κ; Avstår
recall is the weak spot — track it per batch. Probe recall should stay low for
routine votes and spike only for famous ones; anything above ~20% overall
recall means contamination needs more prominent reporting.

## Publishing checkpoints

Any time after step 5 the current state can go live:

```sh
uv run aidag aggregate --run-id full-v1
uv run aidag export-site --run-id full-v1
# commit, then set RUN_ID=full-v1 in .github/workflows/deploy.yml and push
```

The homepage drift chart, timeline and per-case pages all degrade gracefully
with partial coverage (cases without decisions simply show the real votes).

## File map

| Path | What |
|---|---|
| `scripts/agent_batch_workflow.js` | the Workflow script (Sonnet agents, structured output) |
| `pipeline/aidag/agent_run.py` | `agent-prepare` / `agent-status` (checkpoint logic) |
| `pipeline/aidag/ingest_agent_run.py` | `agent-ingest` (idempotent collection) |
| `data/interim/agentrun/{run_id}/` | request files + batch manifests (gitignored, regenerable) |
| `data/results/simulations/{run_id}/` | the scientific record (committed) |
| `docs/methodology.{sv,en}.md` | what the agents may and may not see |
