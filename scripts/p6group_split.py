#!/usr/bin/env python
"""Split the p6group 40-case set into fixed-size groups for a group-size sweep.

  uv run python scripts/p6group_split.py 5     -> g5/g-00.json .. g-07.json, out_g5/

Cases keep their original order and cid, so every arm decides the same 40 cases
and the only variable is how many of them share one agent's context.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

G = Path(__file__).resolve().parents[1] / "data" / "interim" / "p6group"


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cases = json.loads((G / "group.json").read_text())["cases"]
    gdir = G / f"g{n}"
    gdir.mkdir(exist_ok=True)
    (G / f"out_g{n}").mkdir(exist_ok=True)

    chunks = [cases[i:i + n] for i in range(0, len(cases), n)]
    for k, chunk in enumerate(chunks):
        (gdir / f"g-{k:02d}.json").write_text(
            json.dumps({"cases": chunk}, ensure_ascii=False), encoding="utf-8"
        )
    sizes = [len(c) for c in chunks]
    print(f"{len(cases)} cases -> {len(chunks)} groups of {sizes} in {gdir}")
    print(f"out dir: {G / f'out_g{n}'}")


if __name__ == "__main__":
    main()
