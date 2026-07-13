#!/usr/bin/env bash
# One full-v3 batch cycle: prepare -> (workflow runs separately) -> ingest -> repair
# -> verify -> commit. Idempotent and safe to re-run after ANY interruption.
#
# Session limits are not a failure mode here. State is derived from the committed
# JSONL — done = the custom_ids present in data/results/simulations/{run}/ — so
# whatever the agents managed to return gets kept, and the next `prepare` re-issues
# exactly the decisions that are still missing. Nothing needs unwinding, and no
# state file can go stale.
#
#   scripts/run_batch.sh prepare                 # emit the next manifest
#   scripts/run_batch.sh ingest <result.json>    # ingest + repair + verify + commit
set -euo pipefail

RUN_ID=${RUN_ID:-full-v3}
MODEL_ID=${MODEL_ID:-claude-opus-4-8}
BATCH_ID=${BATCH_ID:-claude-code-workflow-p5}
BATCH_SIZE=${BATCH_SIZE:-1600}
CONTEXT_LIMIT=${CONTEXT_LIMIT:-1000000}

cd "$(dirname "$0")/.."

case "${1:-}" in
  prepare)
    uv run aidag agent-prepare --run-id "$RUN_ID" --prompt-version p5 \
      --group 0 --context-limit "$CONTEXT_LIMIT" \
      --batch-size "$BATCH_SIZE" --no-probes
    ;;

  ingest)
    RESULT=${2:?usage: run_batch.sh ingest <result.json>}
    # ingest is idempotent (dedupes on full custom_id), so a partial batch from a
    # session limit is simply kept, and re-running this is harmless.
    uv run aidag agent-ingest --run-id "$RUN_ID" --input "$RESULT" \
      --model "$MODEL_ID" --batch-id "$BATCH_ID"
    uv run aidag repair-citations --run-id "$RUN_ID"
    uv run aidag verify simulate --run-id "$RUN_ID"

    N=$(uv run aidag agent-status --run-id "$RUN_ID" | grep -o '[0-9]*/20312' | head -1)
    git add data/results
    git commit -q -m "$RUN_ID: $N decisions (p5, Opus, size-driven groups)" || echo "nothing new to commit"
    uv run aidag agent-status --run-id "$RUN_ID"
    ;;

  *)
    echo "usage: run_batch.sh {prepare|ingest <result.json>}" >&2
    exit 1
    ;;
esac
