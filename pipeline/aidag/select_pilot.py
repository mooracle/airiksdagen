"""Stratified pilot sample: seeded, reproducible, committed to results/.

Strata: riksmöte x contestedness (whether any party's actual position differed
from the winner — contested votes are the interesting ones, but uncontested
votes must be represented to measure baseline agreement honestly).
"""

from __future__ import annotations

import json
import random
from collections import defaultdict

import polars as pl

from aidag.config import PROCESSED_DIR, RESULTS_DIR


def run(n: int = 100, seed: int = 2026) -> None:
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")

    contested = (
        positions.filter(pl.col("position").is_in(["Ja", "Nej", "Avstår"]))
        .group_by("votering_id")
        .agg(pl.col("position").n_unique().alias("n_positions"))
        .with_columns((pl.col("n_positions") > 1).alias("contested"))
    )
    df = cases.join(contested.select("votering_id", "contested"), on="votering_id", how="left")

    strata: dict[tuple, list[str]] = defaultdict(list)
    for row in df.iter_rows(named=True):
        strata[(row["rm"], bool(row["contested"]))].append(row["votering_id"])

    rng = random.Random(seed)
    total = len(df)
    picked: list[str] = []
    for key in sorted(strata):
        ids = sorted(strata[key])
        quota = max(1, round(n * len(ids) / total))
        rng.shuffle(ids)
        picked.extend(ids[:quota])
    picked = sorted(picked)[:n]

    out = {
        "n": len(picked),
        "seed": seed,
        "strata": {f"{k[0]}|contested={k[1]}": len(v) for k, v in sorted(strata.items())},
        "votering_ids": picked,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "pilot_selection.json").write_text(json.dumps(out, indent=1))
    print(f"pilot: {len(picked)} cases selected (seed {seed}) -> results/pilot_selection.json")
