# Case-metadata orchestration: cheap-model synthesis over all cases

Operational runbook for generating the **per-case metadata layer** — a specific
bilingual `subject`, a why-it-matters `at_stake`, `subtopics`, and a de-leaked
party-blind `agent` view — for all ~2,539 voteringar, using Claude Code
subagents on a subscription (no API key), mirroring the translation pipeline.

The layer is **run-INDEPENDENT** (it describes the case, not any AI run) and
**decoupled**: it changes nothing in `promptgen.py` and does not re-run the
simulation. It produces `agent.{subject,at_stake}` (party-blind, pre-decision)
as data only; wiring that into the prompt is a separate go/no-go (Phase 3).

## Roles

| Role | Model | Responsibility |
|---|---|---|
| Orchestrator | Opus (interactive session) | prepare batch, launch workflow, ingest, verify, spot-check |
| Metadata agent | **Haiku** (`claude-haiku-4-5`, one per request file) | read one self-contained request file → per-case `subject{sv,en}`, `at_stake{sv,en}`, `subtopics[]`, and a party-blind `agent{subject,at_stake}` |

Haiku is enough: the task is grounded synthesis over text supplied in the
request file, not reasoning. ~12 cases/agent → ~212 agents run 16-wide in one
parallel workflow, minutes of wall-clock, negligible budget — and it preserves
the Opus weekly window for the simulation.

## Two grounding blocks (the load-bearing design)

Every request unit carries two blocks so the party-blind fields can never be
synthesized from party-aware text:

- `display_src` (party-aware) → the human `subject` / `at_stake` / `subtopics`
- `agent_src` (**SCRUBBED**: `promptgen.scrub_text(..., "anonymous")` over the
  proposal + reservation texts, author clauses and the structured party list
  dropped) → the `agent.*` fields, the ONLY input they may use

Neither block ever includes the post-decision `notis`. The **guarantee** that
`agent.*` is safe to wire into a prompt later is the scrubbed input; the
`validate_metadata` de-leak asserts (party tag / full party name / author
clause / doc ref / outcome word) are a tested **tripwire**, not the guarantee.

## Checkpoint model — where the state lives

No separate checkpoint file; state is derived (crash-proof, resumable):

- **Done** = `votering_id`s in `data/results/metadata/cases.jsonl`
  (append-only, committed).
- **Pending** = all cases − done. `aidag metadata-prepare` always emits the
  next batch from pending.
- Agents that die return `null` and stay pending → the next `metadata-prepare`
  re-issues exactly the gaps.

## The loop

```bash
# 1. Emit one manifest fanning out over ALL pending cases (~212 Haiku agents).
uv run aidag metadata-prepare --batch-size 400 --per-request 12
#    -> data/interim/metadata/batches/batch-NNN.json  (run-independent: no run_id, no kind)

# 2. Run the batch. From the orchestrator session:
#    Workflow({ scriptPath: "scripts/metadata_batch_workflow.js",
#               args: { manifestPath: "<abs path to batch-NNN.json>" } })
#    One Haiku agent per request file; each file is self-contained.

# 3. Extract the workflow's task-output json['result'] ({cases:[...]}) to a file, then:
uv run aidag metadata-ingest --input <result.json> --model claude-haiku-4-5
#    validates each record (fields + agent-view de-leak), merges the server-side
#    deterministic fields (type/policy_area/committee/counts/parties), dedupes on
#    votering_id, appends with provenance. Idempotent.

# 4. Gate.
uv run aidag verify metadata   # 0 leaky agent views, all ids in parquet, det fields aligned
uv run aidag metadata-status   # done/pending counts

# 5. Backfill: re-run 1-4 until metadata-status shows 0 pending (checkpoint
#    re-issues only the gaps).
```

## Export + publish

```bash
uv run aidag export-site --run-id full-v3   # merges meta into per-case JSON + lean index
cd site && npm run build                    # gate
# commit site/src/data, data/results/metadata, site/public/downloads + the new code
# push to mooracle/airiksdagen (ensure the active gh account is alexsergeyev)
```

The per-case JSON gets the full display metadata under `meta`; the
client-fetched `cases-index.json` stays lean — only `policy_area` + `type`
(filter keys) and one precomputed lowercased `search` blob. Full records,
including `at_stake` and the agent view, ship as
`site/public/downloads/case-metadata.jsonl` (open data).

## Phases (out of scope here)

- **Phase 2 (optional):** fetch reservation full texts + motion contents from
  data.riksdagen.se and regenerate `at_stake` + the agent view with real
  Nej-side substance.
- **Phase 3 (separate go/no-go):** quantify the motion-author leak, and if
  material, scrub motion-author tags in `promptgen.py`, wire `meta.agent.*`
  into the prompt, and re-run the simulation.
