"""Which documents do the agents actually cite, and what does each cost?

Answers the only question that makes corpus trimming safe: a document that is
never cited is dead context and can be dropped losslessly; a document the agents
lean on is earning its tokens, and cutting it would manufacture not_covered.

  uv run python scripts/citation_profile.py full-v3 [full-v2 ...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

import polars as pl

from aidag.config import PROCESSED_DIR, RESULTS_DIR
from aidag.corpus import documents_for


def profile(run_id: str) -> None:
    cases = pl.read_parquet(
        PROCESSED_DIR / "cases.parquet", columns=["votering_id", "datum", "rm"]
    )
    meta = {r["votering_id"]: (r["datum"], r["rm"]) for r in cases.iter_rows(named=True)}

    cited: Counter = Counter()
    served_tokens: dict[str, int] = defaultdict(int)
    served_count: Counter = Counter()
    coverage: Counter = Counter()
    by_party: dict[str, Counter] = defaultdict(Counter)
    n = 0

    for path in sorted((RESULTS_DIR / "simulations" / run_id).glob("*.jsonl")):
        party = path.stem
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            n += 1
            coverage[d["coverage"]] += 1
            datum, rm = meta.get(d["votering_id"], ("", ""))
            for kind, _tag, text in documents_for(
                party, datum, rm, d["votering_id"], d["prompt_version"]
            ):
                served_tokens[kind] += len(text) // 4  # served once per decision's context
                served_count[kind] += 1
            for c in d.get("citations", []):
                cited[c["document"]] += 1
                by_party[party][c["document"]] += 1

    total_cites = sum(cited.values())
    print(f"\n=== {run_id} — {n} decisions, {total_cites} citations")
    print(f"coverage: {dict(coverage)}\n")
    print(f"{'document':14} {'served':>8} {'cited':>7} {'share':>7} {'avg tok served':>15} {'cites/1k tok':>13}")
    print("-" * 70)
    for kind in ("valmanifest", "partiprogram", "tidoavtalet", "budgetmotion"):
        if not served_count[kind]:
            continue
        avg_tok = served_tokens[kind] / served_count[kind]
        share = cited[kind] / total_cites * 100 if total_cites else 0
        # citations earned per 1k tokens of context spent — the efficiency number
        eff = cited[kind] / (avg_tok / 1000) if avg_tok else 0
        print(
            f"{kind:14} {served_count[kind]:8} {cited[kind]:7} {share:6.1f}% "
            f"{avg_tok:15,.0f} {eff:13.2f}"
        )

    print("\nby party (citation counts):")
    for p, c in sorted(by_party.items()):
        print(f"  {p:3} {dict(c)}")


if __name__ == "__main__":
    for run_id in sys.argv[1:] or ["full-v3"]:
        profile(run_id)
