"""Aggregate simulation results vs actual positions into site-ready statistics.

Outputs data/results/aggregates/{run_id}/:
  gap.json               plan-vs-behaviour gap, tiered (p6 runs only; see gap.py)
  summary.json           vote agreement + Cohen's kappa — CALIBRATION, not a score
  party_timeseries.json  per party x month agreement %
  confusion.json         3x3 (AI rost x actual position) per party
  coalition.json         coalition baseline vs party programme (see coalition.py)

On a p6 run the product is `gap.json`. `rost` is DERIVED from the plan's stance
(promptgen.derive_rost) and the party's real vote is out-of-sample context, so
"agreement" here measures how often a party voted its own plan — not how well
the model predicted. Read summary.json as calibration and gap.json as the
result. On p4/p5 runs there is no `hallning` and gap.json is not written.
"""

from __future__ import annotations

import json
from collections import Counter

import polars as pl

from aidag import coalition, gap
from aidag.config import PARTY_CODES, PROCESSED_DIR, RESULTS_DIR

SIM_LABELS = ["Ja", "Nej", "Avstår"]


def load_decisions(run_id: str) -> pl.DataFrame:
    sim_dir = RESULTS_DIR / "simulations" / run_id
    rows = []
    for path in sorted(sim_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise FileNotFoundError(f"no decisions found in {sim_dir}")
    return pl.DataFrame(rows)


def cohens_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Kappa over (predicted, actual) label pairs."""
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({l for p in pairs for l in p})
    po = sum(1 for a, b in pairs if a == b) / n
    pred_counts = Counter(a for a, _ in pairs)
    act_counts = Counter(b for _, b in pairs)
    pe = sum(pred_counts[l] * act_counts[l] for l in labels) / (n * n)
    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def run(run_id: str) -> None:
    decisions = load_decisions(run_id)
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").select(
        "votering_id", "datum", "utskott", "rubrik"
    )

    df = (
        decisions.join(positions, on=["votering_id", "parti"], how="inner")
        .join(cases, on="votering_id", how="left")
        # A party that was absent has no comparable position; excluded from agreement.
        .filter(pl.col("position") != "Frånvarande")
        .with_columns(
            (pl.col("rost") == pl.col("position")).alias("agree"),
            pl.col("datum").str.slice(0, 7).alias("month"),
        )
    )

    out_dir = RESULTS_DIR / "aggregates" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- summary ---
    per_party = {}
    for p in PARTY_CODES:
        sub = df.filter(pl.col("parti") == p)
        if len(sub) == 0:
            continue
        pairs = list(zip(sub["rost"].to_list(), sub["position"].to_list()))
        per_party[p] = {
            "n": len(sub),
            "agreement": round(float(sub["agree"].mean()), 4),
            "kappa": round(cohens_kappa(pairs), 4) if cohens_kappa(pairs) is not None else None,
            "avstar_recall": _avstar_recall(sub),
        }
    summary = {
        "run_id": run_id,
        "n_decisions": len(df),
        "overall_agreement": round(float(df["agree"].mean()), 4),
        "per_party": per_party,
        "models": sorted(df["model"].unique().to_list()),
        "arms": sorted(df["arm"].unique().to_list()),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))

    # --- timeseries ---
    ts = (
        df.group_by("parti", "month")
        .agg(pl.col("agree").mean().alias("agreement"), pl.len().alias("n"))
        .sort("parti", "month")
    )
    (out_dir / "party_timeseries.json").write_text(json.dumps(ts.to_dicts(), indent=1))

    # --- confusion matrices ---
    confusion: dict[str, dict] = {}
    for p in PARTY_CODES:
        sub = df.filter(pl.col("parti") == p)
        matrix = {ai: {act: 0 for act in SIM_LABELS} for ai in SIM_LABELS}
        for ai, act in zip(sub["rost"].to_list(), sub["position"].to_list()):
            if ai in matrix and act in SIM_LABELS:
                matrix[ai][act] += 1
        confusion[p] = matrix
    (out_dir / "confusion.json").write_text(json.dumps(confusion, indent=1))

    # --- plan-vs-behaviour gap (p6 product; None on p4/p5 runs) ---
    gap_out = gap.compute(df)
    if gap_out is not None:
        gap_out["run_id"] = run_id
        (out_dir / "gap.json").write_text(json.dumps(gap_out, indent=1, ensure_ascii=False))

    # --- coalition discipline vs party programme ---
    coal = coalition.compute(df, run_id)
    coal["run_id"] = run_id
    (out_dir / "coalition.json").write_text(json.dumps(coal, indent=1, ensure_ascii=False))

    print(f"aggregates written to {out_dir}")
    if gap_out is not None:
        print("\n  PLAN-vs-BEHAVIOUR GAP (the product):")
        print(gap.report(gap_out))
        print("\n  vote agreement below is CALIBRATION ONLY — not a score:")
    print(f"  overall agreement: {summary['overall_agreement']:.1%} over {summary['n_decisions']} decisions")
    for p, s in per_party.items():
        print(f"  {p}: {s['agreement']:.1%} (kappa {s['kappa']}, n={s['n']})")

    print("\n  coalition baseline vs party programme:")
    print(f"  {'':4} {'coalition':>10} {'programme':>10} {'lift':>8}  {'override (strict)':>18}")
    for p, s in coal["per_party"].items():
        strict = s["override"]["strict"]["all"]
        ov = f"{strict['num']}/{strict['den']}" if strict["den"] else "—"
        print(
            f"  {p:4} {s['coalition_baseline']:9.1%} {s['programme_accuracy']:9.1%} "
            f"{s['programme_lift']:+7.1%}  {ov:>18}"
        )


def _avstar_recall(sub: pl.DataFrame) -> float | None:
    avstar = sub.filter(pl.col("position") == "Avstår")
    if len(avstar) == 0:
        return None
    return round(float((avstar["rost"] == "Avstår").mean()), 4)


