"""Download bulk votering dumps and flatten them into votes.parquet.

Each zip (one per riksmöte) contains one JSON file per votering, named
``{dok_id}-{punkt}-{votering_id}.json`` with root ``dokvotering.votering`` =
list of ~349 per-MP rows. dok_id only exists in the filename, so we carry it
in from there.
"""

from __future__ import annotations

import json
import re
import zipfile

import httpx
import polars as pl

from aidag.config import PROCESSED_DIR, RAW_DIR, RIKSMOTEN, VOTERING_ZIP_URL, rm_slug

VOTERING_RAW_DIR = RAW_DIR / "votering"
VOTES_PARQUET = PROCESSED_DIR / "votes.parquet"

FILENAME_RE = re.compile(r"^(?P<dok_id>.+?)-(?P<punkt>\d+)-(?P<uuid>[0-9A-Fa-f-]{36})\.json$")

ROW_FIELDS = [
    "rm", "beteckning", "votering_id", "punkt", "namn", "intressent_id",
    "parti", "valkrets", "rost", "avser", "votering", "banknummer", "datum",
]


def download_zips(force: bool = False) -> list:
    VOTERING_RAW_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for rm in RIKSMOTEN:
        url = VOTERING_ZIP_URL.format(rm=rm_slug(rm))
        path = VOTERING_RAW_DIR / f"votering-{rm_slug(rm)}.json.zip"
        if path.exists() and not force:
            print(f"  {path.name}: exists, skipping")
        else:
            print(f"  {path.name}: downloading {url}")
            with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        paths.append(path)
    return paths


def parse_zip(path) -> list[dict]:
    rows: list[dict] = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            m = FILENAME_RE.match(name)
            if not m:
                print(f"  WARNING: unexpected filename {name} in {path.name}, skipping")
                continue
            data = json.loads(z.open(name).read().decode("utf-8-sig"))
            votering = data.get("dokvotering", {}).get("votering", [])
            if isinstance(votering, dict):  # single-row lists get collapsed
                votering = [votering]
            for r in votering:
                row = {k: r.get(k) for k in ROW_FIELDS}
                row["dok_id"] = m["dok_id"]
                rows.append(row)
    return rows


def run(force: bool = False) -> None:
    paths = download_zips(force=force)
    all_rows: list[dict] = []
    for path in paths:
        rows = parse_zip(path)
        print(f"  {path.name}: {len(rows)} MP-vote rows")
        all_rows.extend(rows)

    df = pl.DataFrame(all_rows, infer_schema_length=None).with_columns(
        pl.col("punkt").cast(pl.Int32),
        pl.col("votering_id").str.to_uppercase(),
    )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(VOTES_PARQUET)
    n_vot = df.select(pl.col("votering_id").n_unique()).item()
    print(f"votes.parquet: {len(df)} rows, {n_vot} voteringar -> {VOTES_PARQUET}")
