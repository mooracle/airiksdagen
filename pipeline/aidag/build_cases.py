"""Join votes.parquet with dokumentstatus into cases + actual party positions.

Outputs:
  data/processed/cases.parquet            one row per votering (the simulation unit)
  data/processed/party_positions.parquet  actual behaviour per votering × party
  data/processed/orphans.json             voteringar that failed to join (quarantined)
"""

from __future__ import annotations

import json
import re
from html import unescape

import polars as pl

from aidag.config import PARTY_CODES, PROCESSED_DIR
from aidag.fetch_cases import DOKSTATUS_DIR
from aidag.fetch_votes import VOTES_PARQUET

CASES_PARQUET = PROCESSED_DIR / "cases.parquet"
POSITIONS_PARQUET = PROCESSED_DIR / "party_positions.parquet"
ORPHANS_JSON = PROCESSED_DIR / "orphans.json"

TAG_RE = re.compile(r"<[^>]+>")


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _clean_html(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("<br />", "\n").replace("<BR/>", "\n").replace("<br>", "\n")
    text = TAG_RE.sub("", text)
    # Riksdag HTML fields arrive entity-encoded ('f&ouml;rslag', '&sect;', '&#173;').
    # Tags are stripped above but entities are not, so the full case text reaches
    # the agent prompt (and the site) mangled. Decode them; drop the soft hyphens
    # &#173; injects mid-word (as the corpus normalizer does) and fold nbsp to a
    # real space so downstream whitespace collapsing works.
    text = unescape(text)
    text = text.replace("­", "").replace(" ", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _parse_partier(raw: str | None) -> list[str]:
    """'"C"' or '"S","V","MP"' -> ['C', 'S', ...]."""
    if not raw:
        return []
    return [p.strip().strip('"').upper() for p in raw.split(",") if p.strip().strip('"')]


def load_dokstatus(dok_id: str) -> dict | None:
    path = DOKSTATUS_DIR / f"{dok_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["dokumentstatus"]


def extract_forslag(ds: dict) -> dict[str, dict]:
    """Map votering_id (uppercase) -> utskottsforslag entry for this document."""
    out: dict[str, dict] = {}
    for uf in _as_list(ds.get("dokutskottsforslag", {}).get("utskottsforslag")):
        vid = (uf.get("votering_id") or "").upper()
        if vid:
            out[vid] = uf
    return out


def extract_uppgift(ds: dict, kod: str) -> str:
    for u in _as_list(ds.get("dokuppgift", {}).get("uppgift")):
        if u.get("kod") == kod:
            return (u.get("text") or "").strip()
    return ""


def build_alternatives(uf: dict, ds: dict) -> list[dict]:
    """Committee proposal + the reservations put against it in this vote."""
    alts = [{
        "alt_id": "utskottet",
        "text": _clean_html(uf.get("forslag")),
        "source_partier": [],
    }]
    res_numbers = {n.strip() for n in (uf.get("motforslag_nummer") or "").split(",") if n.strip()}
    punkt = str(uf.get("punkt") or "")
    for mf in _as_list(ds.get("dokmotforslag", {}).get("motforslag")):
        nummer = str(mf.get("nummer") or "")
        if str(mf.get("utskottsforslag_punkt") or "") != punkt:
            continue
        if res_numbers and nummer not in res_numbers:
            continue
        alts.append({
            "alt_id": f"res-{nummer}",
            "text": _clean_html(mf.get("rubrik")) or f"Reservation {nummer}",
            "source_partier": _parse_partier(mf.get("partier")),
        })
    return alts


def extract_references(ds: dict, limit: int = 20) -> list[dict]:
    """Documents the betänkande deals with (propositions, motions) with ids
    for deep links. referenstyp 'behandlar' = under consideration."""
    refs = []
    for r in _as_list(ds.get("dokreferens", {}).get("referens")):
        if r.get("referenstyp") != "behandlar" or not r.get("ref_dok_id"):
            continue
        refs.append({
            "dok_id": r["ref_dok_id"],
            "typ": r.get("ref_dok_typ") or "",
            "label": f"{r.get('ref_dok_dokumentnamn') or ''} {r.get('ref_dok_rm') or ''}:{r.get('ref_dok_bet') or ''}".strip(),
            "titel": (r.get("ref_dok_titel") or "").strip(),
            "undertitel": (r.get("ref_dok_subtitel") or "").strip(),
        })
        if len(refs) == limit:
            break
    return refs


def build_party_positions(votes: pl.DataFrame) -> pl.DataFrame:
    """Aggregate per-MP sakfråga votes into party positions."""
    counts = (
        votes.group_by(["votering_id", "parti", "rost"])
        .len()
        .pivot(on="rost", index=["votering_id", "parti"], values="len")
        .fill_null(0)
    )
    for col in ["Ja", "Nej", "Avstår", "Frånvarande"]:
        if col not in counts.columns:
            counts = counts.with_columns(pl.lit(0).cast(pl.UInt32).alias(col))
    cast_cols = ["Ja", "Nej", "Avstår"]
    counts = counts.with_columns(
        (pl.col("Ja") + pl.col("Nej") + pl.col("Avstår")).alias("n_cast"),
        (pl.col("Ja") + pl.col("Nej") + pl.col("Avstår") + pl.col("Frånvarande")).alias("seats"),
        pl.max_horizontal(cast_cols).alias("n_max"),
    )
    position = (
        pl.when(pl.col("n_cast") == 0).then(pl.lit("Frånvarande"))
        .when(pl.col("Ja") == pl.col("n_max")).then(pl.lit("Ja"))
        .when(pl.col("Nej") == pl.col("n_max")).then(pl.lit("Nej"))
        .otherwise(pl.lit("Avstår"))
    )
    cohesion = (
        pl.when(pl.col("n_cast") == 0).then(1.0)
        .otherwise(pl.col("n_max") / pl.col("n_cast"))
    )
    return counts.select(
        "votering_id",
        "parti",
        pl.col("Ja").alias("n_ja"),
        pl.col("Nej").alias("n_nej"),
        pl.col("Avstår").alias("n_avstar"),
        pl.col("Frånvarande").alias("n_franvarande"),
        position.alias("position"),
        cohesion.alias("cohesion"),
        "seats",
    )


def run() -> None:
    votes = (
        pl.read_parquet(VOTES_PARQUET)
        .filter(pl.col("avser") == "sakfrågan", pl.col("votering") == "huvud")
        .with_columns(pl.col("parti").str.to_uppercase())
    )
    votes = votes.filter(pl.col("parti").is_in(PARTY_CODES))

    voteringar = (
        votes.group_by("votering_id")
        .agg(
            pl.col("dok_id").first(),
            pl.col("rm").first(),
            pl.col("beteckning").first(),
            pl.col("punkt").first(),
            pl.col("datum").max().alias("datum"),
        )
        .sort("datum", "votering_id")
    )

    cases: list[dict] = []
    orphans: list[dict] = []
    ds_cache: dict[str, dict | None] = {}
    for row in voteringar.iter_rows(named=True):
        dok_id = row["dok_id"]
        if dok_id not in ds_cache:
            ds_cache[dok_id] = load_dokstatus(dok_id)
        ds = ds_cache[dok_id]
        uf = extract_forslag(ds).get(row["votering_id"]) if ds else None
        if ds is None or uf is None:
            orphans.append({**row, "reason": "missing dokumentstatus" if ds is None else "no utskottsforslag match"})
            continue
        dok = ds["dokument"]
        cases.append({
            "votering_id": row["votering_id"],
            "rm": row["rm"],
            "beteckning": row["beteckning"],
            "punkt": row["punkt"],
            "dok_id": dok_id,
            "datum": row["datum"][:10],
            "utskott": dok.get("organ") or "",
            "rubrik": (uf.get("rubrik") or "").strip() or (dok.get("titel") or ""),
            "dok_titel": dok.get("titel") or "",
            "forslag_text": _clean_html(uf.get("forslag")),
            # notis is written AFTER the decision ("Riksdagen sa ja…") — display only,
            # never in prompts. utsknotis is the committee's pre-decision summary.
            "notis": _clean_html(extract_uppgift(ds, "notis")),
            "utsknotis": _clean_html(extract_uppgift(ds, "utsknotis")),
            "beslut_sammanfattning": extract_uppgift(ds, "beslutssammanfattningusk"),
            "vinnare": uf.get("vinnare") or "",
            "alternatives": json.dumps(build_alternatives(uf, ds), ensure_ascii=False),
            "references": json.dumps(extract_references(ds), ensure_ascii=False),
            "kb_month": row["datum"][:7],
        })

    cases_df = pl.DataFrame(cases)
    cases_df.write_parquet(CASES_PARQUET)
    ORPHANS_JSON.write_text(json.dumps(orphans, ensure_ascii=False, indent=1))

    valid_ids = cases_df["votering_id"]
    positions = build_party_positions(votes.filter(pl.col("votering_id").is_in(valid_ids)))
    positions.write_parquet(POSITIONS_PARQUET)

    print(f"cases.parquet: {len(cases_df)} cases ({len(orphans)} orphans quarantined)")
    print(f"party_positions.parquet: {len(positions)} rows")
