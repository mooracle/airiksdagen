"""Real-vote analytics — computed purely from actual votes, no AI involved.

Called by export_site; results land in site/src/data/aggregates/.

  pairs_matrix.json    8x8 party-pair agreement (both cast, same position)
  gov_defeats.json     voteringar where the M+KD+L line lost the chamber
  cohesion.json        per party x month: internal cohesion + absence share
  dissenters.json      MPs most often voting against their party line
"""

from __future__ import annotations

import polars as pl

from aidag.config import PARTY_CODES, PROCESSED_DIR

GOV = ["M", "KD", "L"]


def _positions() -> pl.DataFrame:
    return pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")


def _votes() -> pl.DataFrame:
    return (
        pl.read_parquet(PROCESSED_DIR / "votes.parquet")
        .filter(pl.col("avser") == "sakfrågan", pl.col("votering") == "huvud")
        .with_columns(pl.col("parti").str.to_uppercase())
        .filter(pl.col("parti").is_in(PARTY_CODES))
    )


def pairs_matrix() -> dict:
    """% of voteringar where two parties took the same cast position."""
    pos = _positions().filter(pl.col("position") != "Frånvarande")
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").select("votering_id", "rm")
    pos = pos.join(cases, on="votering_id")

    def matrix_for(df: pl.DataFrame) -> dict:
        by_vote: dict[str, dict[str, str]] = {}
        for r in df.iter_rows(named=True):
            by_vote.setdefault(r["votering_id"], {})[r["parti"]] = r["position"]
        agree = {a: {b: 0 for b in PARTY_CODES} for a in PARTY_CODES}
        total = {a: {b: 0 for b in PARTY_CODES} for a in PARTY_CODES}
        for positions in by_vote.values():
            parties = [p for p in PARTY_CODES if p in positions]
            for i, a in enumerate(parties):
                for b in parties[i:]:
                    total[a][b] += 1
                    total[b][a] += 1
                    if positions[a] == positions[b]:
                        agree[a][b] += 1
                        agree[b][a] += 1
        return {
            a: {
                b: round(agree[a][b] / total[a][b], 3) if total[a][b] else None
                for b in PARTY_CODES
            }
            for a in PARTY_CODES
        }

    out = {"overall": matrix_for(pos), "per_rm": {}}
    for rm in sorted(pos["rm"].unique()):
        out["per_rm"][rm] = matrix_for(pos.filter(pl.col("rm") == rm))
    return out


def gov_defeats() -> list[dict]:
    """Voteringar where M, KD and L shared a cast position and the chamber
    went the other way (the government side lost)."""
    pos = _positions()
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").select(
        "votering_id", "datum", "rubrik", "dok_titel", "rm", "beteckning", "punkt"
    )
    totals = (
        pos.group_by("votering_id")
        .agg(pl.col("n_ja").sum().alias("ja"), pl.col("n_nej").sum().alias("nej"))
        .with_columns(
            pl.when(pl.col("ja") > pl.col("nej")).then(pl.lit("Ja")).otherwise(pl.lit("Nej")).alias("outcome")
        )
    )
    gov_pos = (
        pos.filter(pl.col("parti").is_in(GOV))
        .group_by("votering_id")
        .agg(pl.col("position").n_unique().alias("n_lines"), pl.col("position").first().alias("gov_pos"))
        .filter((pl.col("n_lines") == 1) & pl.col("gov_pos").is_in(["Ja", "Nej"]))
    )
    defeats = (
        gov_pos.join(totals, on="votering_id")
        .filter(pl.col("gov_pos") != pl.col("outcome"))
        .join(cases, on="votering_id")
        .sort("datum", descending=True)
    )
    return [
        {
            "votering_id": r["votering_id"],
            "datum": r["datum"],
            "rubrik": r["rubrik"] or r["dok_titel"],
            "bet": f"{r['rm']}:{r['beteckning']} p.{r['punkt']}",
            "gov_pos": r["gov_pos"],
            "outcome": r["outcome"],
        }
        for r in defeats.iter_rows(named=True)
    ]


def cohesion_series() -> list[dict]:
    """Per party x month: mean internal cohesion and absence share."""
    pos = _positions()
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet").select("votering_id", "datum")
    df = (
        pos.join(cases, on="votering_id")
        .with_columns(
            pl.col("datum").str.slice(0, 7).alias("month"),
            (pl.col("n_franvarande") / pl.col("seats")).alias("absence"),
        )
        .filter(pl.col("position") != "Frånvarande")
        .group_by("parti", "month")
        .agg(
            pl.col("cohesion").mean().round(4).alias("cohesion"),
            pl.col("absence").mean().round(4).alias("absence"),
            pl.len().alias("n"),
        )
        .sort("parti", "month")
    )
    return df.to_dicts()


def dissenter_league(top: int = 30) -> list[dict]:
    """MPs most often voting against their party's majority position."""
    votes = _votes()
    pos = _positions().select("votering_id", "parti", "position")
    j = votes.join(pos, on=["votering_id", "parti"])
    cast = j.filter((pl.col("rost") != "Frånvarande") & (pl.col("position") != "Frånvarande"))
    per_mp = (
        cast.group_by("namn", "parti")
        .agg(
            pl.len().alias("n_cast"),
            (pl.col("rost") != pl.col("position")).sum().alias("n_dissent"),
        )
        .filter(pl.col("n_dissent") > 0)
        .with_columns((pl.col("n_dissent") / pl.col("n_cast")).round(4).alias("rate"))
        # group_by row order is not deterministic, so a single-key sort left the
        # top-N cutoff to chance whenever MPs tied on n_dissent — the file churned
        # on every export. Rate breaks ties meaningfully (same dissents, fewer
        # votes cast = dissents more often); namn makes the result reproducible.
        .sort(["n_dissent", "rate", "namn"], descending=[True, True, False])
        .head(top)
    )
    return per_mp.to_dicts()
