# Policy-first grouped run (`full-v4`) — operator runbook

The single document needed to execute the **complete policy-first simulation**:
all 2,539 voteringar × 8 parties (20,312 decisions) under prompt **p6**, every
decision produced the same way — one grouped Opus 5 agent per ≤60 same-party,
same-context cases. Supersedes `orchestration-grouped-run.md` (full-v2, p4,
Sonnet, group 8) for execution.

Run from an interactive Claude Code session (the orchestrator); no API key needed.

## Run identity — fixed for the whole run

| | |
|---|---|
| run_id | `full-v4` |
| prompt | **p6** (policy-first; `hallning`, not `rost`) |
| arm | `anonymous` |
| corpus | valmanifest + partiprogram only (`DOCS_P6`) — party-blind, no Tidöavtalet, no budget |
| execution | grouped, `--group 60 --batch-size 3000` |
| model | **`claude-opus-5`**, effort **`high`** |
| write chunk | 10 (set automatically for p6) |
| batch_id at ingest | `claude-code-workflow-p6-opus5` |
| workflow script | `scripts/grouped_batch_workflow.js` |

## Why this configuration

Every value above was measured, not chosen — see
`docs/grouping-cost-quality-study.md`. The short version:

- **Grouped, not per-case.** The corpus is ~30–42k tokens and is read once per
  agent; a per-case agent re-reads it every time. 7–14× cheaper.
- **n≈60.** Cost falls monotonically with group size and flattens by ~40.
- **Opus 5 / `high`.** Against Opus 4.8 on identical cases: 25% cheaper, 38%
  faster, 17% fewer tokens, citations and grounding unchanged. `medium` costs
  the same and drifts; `xhigh` costs 48% more and returned identical decisions.
- **Chunk 10.** Chunk 20 cost 13% more for identical quality; chunk 5 added a
  second cache blowout.
- **3–4 citations, decisive-first.** Lifted citation density 2.47 → 3.95 per
  decision at *identical* cost — the expense is the corpus search, not the citing.

Expected: **~$2,465** and ~150 h serial ≈ **~20 h wall**, across **358 agents**
in ~7 batches. (Batch 1 measured $0.121/decision — 15% above the $0.105
single-agent point; see the production section of the study for why. The
pre-run projection of ~$2,200 was optimistic.)

## Prerequisites (verify once per session)

```sh
uv run aidag verify cases          # source data intact
uv run aidag verify prompts        # golden tests + anonymous-arm leak scan
uv run aidag verify casemeta       # the layer p6 actually reads
uv run aidag verify reservations   # motförslag substance
```

All four must pass. `verify casemeta` includes a **completeness** check, not just
validity: every votering with a real counter-proposal must have a non-empty
`agent.alternatives`. Without it, a case with an empty Nej side silently falls
back to the p5 hollow ~92-char Ja-only brief — 42 cases were in that state and
were regenerated on Opus 5 (2026-07-25); coverage is now 2,529/2,539 (99.6%).

The residual **10** are voteringar whose only alternative is `utskottet` — there
is genuinely no counter-proposal, so the p5 fallback is correct for them. That is
80 decisions across 8 parties, decided on a one-sided brief by necessity rather
than by defect. Worth stating in the methodology page rather than leaving implicit.

`uv run aidag verify metadata` currently **fails** with
"2 misaligned" — that is the **legacy** `data/results/metadata/` store, which the
unified casemeta layer superseded. p6 builds its case block from
`casemeta` (`promptgen._casemeta_agent`), so it does not gate this run. Refresh
or retire that store separately; do not let a permanently-red gate train you to
ignore gates.

## Three traps, each of which silently costs money

1. **`--batch-size` caps the batch *before* grouping.** The 20,312 decisions live
   in 34 (party, p6-context) buckets, so a small batch leaves only a handful of
   cases per bucket and `--group 60` silently yields groups of 7–15. At that size
   the fixed corpus read dominates and cost roughly doubles. **Verify the size
   distribution after every prepare** (command below); expect a median of 60.
2. **`PROMPT_VERSION` in `config.py` is now `p6`** (it was `p5` through batch 18).
   `agent-prepare` and `agent-status` both default to it, so the bare commands are
   finally correct for this run and the `--prompt-version p6` in the examples below
   is redundant — harmless, and kept because it is explicit. The flag still matters
   in reverse: pass `--prompt-version p5` when touching full-v3 or earlier, or a p6
   manifest builds the wrong corpus *and* sizes groups with p6 case lengths.
3. **`agent-ingest --model` defaults to `claude-sonnet-4-6`.** Pass
   `--model claude-opus-5` or the run's provenance is wrong for every decision.
   This one is still live — it is the remaining default that does not match the run.

The workflow itself refuses to infer a model on p6 rather than falling back to
Sonnet — a wrong-model full run is a ~$2k, 18-hour mistake that only shows up at
analysis time.

## The loop (repeat until `agent-status` reports 0 pending)

### 1. Prepare a batch

```sh
uv run aidag agent-prepare --run-id full-v4 --prompt-version p6 \
    --group 60 --batch-size 3000
```

Check the group sizes before running anything:

```sh
uv run python - <<'PY'
import json, pathlib, collections
p = sorted(pathlib.Path("data/interim/agentrun/full-v4/batches").glob("batch-*.json"))[-1]
m = json.loads(p.read_text())
sizes = [len(i["cids"]) for i in m["items"] if i["kind"] == "simgroup"]
print(p.name, "| groups:", len(sizes), "| cases:", sum(sizes))
print("sizes:", dict(sorted(collections.Counter(sizes).items())))
print("median:", sorted(sizes)[len(sizes)//2], "(expect 60)")
PY
```

If the median is not ~60, raise `--batch-size` and re-prepare.

### 2. Run the batch

```
Workflow({ scriptPath: "scripts/grouped_batch_workflow.js",
           args: { manifestPath: "<abs path to batch-NNN.json>",
                   model: "claude-opus-5", effort: "high" } })
```

Agents write decisions as JSONL under the batch's `out_dir`; the workflow
returns only receipts and probes. Agents that error leave their cids pending —
the next `agent-prepare` re-issues them, so a partial batch is not a problem.

### 3. Merge and ingest

```sh
uv run aidag agent-merge  --run-id full-v4
uv run aidag agent-ingest --run-id full-v4 --input <merged.json> \
    --model claude-opus-5 --batch-id claude-code-workflow-p6-opus5
uv run aidag agent-status --run-id full-v4
```

`agent-ingest` validates every decision against `DECISION_SCHEMA_P6`; malformed
ones are skipped and their cids re-issue on the next prepare.

## Metering a batch (optional, recommended on batch 1)

```sh
uv run python scripts/agent_meter.py full-v4=batch1=3000
```

Confirms $/decision against the $0.105 operating point before committing the
remaining ~17k decisions. If batch 1 comes in materially above ~$0.12/decision,
check the group-size distribution first — that is nearly always the cause.

## After the run

```sh
uv run aidag aggregate   --run-id full-v4      # gap metrics, not accuracy
uv run aidag export-site --run-id full-v4
```

Note `full-v3` stays frozen for comparison; p6 is backwards-incompatible by
choice (every reader of `rost` changes to `hallning` + derived vote).

## What the run produces, and what it does not

The product is the **plan-vs-behaviour gap**: what a party's own published plan
implies, next to how it actually voted. It is *not* a vote predictor, and must
not be presented as one — on party V the derived vote scores 56–60%, and that
"failure" **is** the 40–44% gap being reported.

Validated on V 2024 (n=60, the hardest party): gap 44%, 21 of 22 in the
plan→Nej-but-voted-Ja direction, 237/237 citations verbatim, and gap rate rising
monotonically as the model's own coverage/confidence falls (explicit 29% →
inferred 52%; high 29% → medium 48% → low 67%). Cross-model agreement with the
two Opus 4.8 passes is 94–96%, against 98% within-model reproducibility — the
stance is a property of the documents, not of the model.

Gap **rate** will vary hugely by party: governing parties (plan→Ja, voted Ja)
should approach 0%, opposition parties high. A near-0% gap for M/KD/L is the
expected result, not a bug.
