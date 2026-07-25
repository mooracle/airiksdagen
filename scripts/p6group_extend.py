#!/usr/bin/env python
"""Extend the p6group case set to N, keeping the original 40 as a prefix.

  uv run python scripts/p6group_extend.py 60

The existing 40 cases keep their cids (000-039), so any arm run on the extended
set still scores against the earlier arms on those cases; the added cases get
cids 040+. Every added case is drawn from the SAME p6 context ('M-2021-tido'),
so the already-built system/M.txt applies unchanged — no new corpus to render.

Writes group<N>.json (the group file) and manifest<N>.json (cid -> votering_id).
The original group.json / manifest.json are left untouched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import polars as pl  # noqa: E402

from aidag.config import PROCESSED_DIR  # noqa: E402
from aidag.corpus import context_key  # noqa: E402
from aidag.promptgen import render_user_message  # noqa: E402

G = Path(__file__).resolve().parents[1] / "data" / "interim" / "p6group"
VERSION, ARM, PARTY = "p6", "anonymous", "M"


def main() -> None:
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    base_cases = json.loads((G / "group.json").read_text())["cases"]
    base_man = json.loads((G / "manifest.json").read_text())
    used = {m["votering_id"] for m in base_man}
    need = target - len(base_cases)
    if need <= 0:
        print(f"already have {len(base_cases)} cases; nothing to add")
        return

    cases = {c["votering_id"]: c for c in
             pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)}
    key = context_key(PARTY, cases[base_man[0]["votering_id"]]["datum"],
                      cases[base_man[0]["votering_id"]]["rm"],
                      base_man[0]["votering_id"], VERSION)

    pool = sorted(
        (vid for vid, c in cases.items()
         if vid not in used
         and context_key(PARTY, c["datum"], c["rm"], vid, VERSION) == key),
        key=lambda v: (cases[v]["datum"], v),
    )
    if len(pool) < need:
        raise SystemExit(f"only {len(pool)} candidates in context {key}, need {need}")

    # deterministic spread across the pool rather than the first N, so the added
    # cases are not all clustered in one part of the mandate period
    step = max(1, len(pool) // need)
    picked = pool[::step][:need]

    out_cases = list(base_cases)
    out_man = list(base_man)
    for k, vid in enumerate(picked):
        cid = f"{len(base_cases) + k:03d}"
        case = cases[vid]
        out_cases.append({
            "cid": cid,
            "user": render_user_message(case, ARM, VERSION),
        })
        out_man.append({"id": cid, "votering_id": vid, "parti": PARTY,
                        "pos": len(base_cases) + k, "datum": case["datum"]})

    (G / f"group{target}.json").write_text(
        json.dumps({"cases": out_cases}, ensure_ascii=False), encoding="utf-8")
    (G / f"manifest{target}.json").write_text(
        json.dumps(out_man, ensure_ascii=False, indent=1), encoding="utf-8")
    (G / f"out_g{target}").mkdir(exist_ok=True)

    dts = sorted(m["datum"] for m in out_man)
    print(f"group{target}.json: {len(out_cases)} cases (kept {len(base_cases)}, "
          f"added {need}) in context {key}")
    print(f"  dates {dts[0]} -> {dts[-1]}")
    print(f"  out dir: {G / f'out_g{target}'}")


if __name__ == "__main__":
    main()
