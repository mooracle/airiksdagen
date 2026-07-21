#!/usr/bin/env python
"""Merge per-agent translation output files into one translate-ingest input.

The Haiku translate workflow has each agent write out/<name>.json as
{"kind":"cases","units":[...]}. This collects every out/*.json for a run into a
single {"cases":[...],"decisions":[...]} file for `aidag translate-ingest`.
Malformed files are reported and skipped — they stay pending for the next cycle.

Usage: uv run python scripts/merge_translations.py <run_id> <out_file>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from aidag.config import INTERIM_DIR


def main() -> None:
    run_id, out_file = sys.argv[1], sys.argv[2]
    out_dir = INTERIM_DIR / "translate" / run_id / "out"
    cases: list[dict] = []
    decisions: list[dict] = []
    n_files = n_bad = 0
    for p in sorted(out_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            units = data["units"]
            (cases if data.get("kind") == "cases" else decisions).extend(units)
            n_files += 1
        except Exception as e:  # noqa: BLE001
            n_bad += 1
            print(f"  skipped {p.name}: {e}")
    Path(out_file).write_text(
        json.dumps({"cases": cases, "decisions": decisions}, ensure_ascii=False)
    )
    print(f"merged {n_files} files ({n_bad} bad) -> {len(cases)} cases + {len(decisions)} decisions -> {out_file}")


if __name__ == "__main__":
    main()
