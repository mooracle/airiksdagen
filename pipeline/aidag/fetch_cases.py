"""Fetch dokumentstatus JSON for every dok_id referenced by a vote.

One file per dok_id in data/raw/dokumentstatus/; existing files are skipped so
the command is resumable. Failures are recorded in _failed.json and retried
with --retry-failed.
"""

from __future__ import annotations

import json
import time

import httpx
import polars as pl

from aidag.config import DOKUMENTSTATUS_URL, RAW_DIR
from aidag.fetch_votes import VOTES_PARQUET

DOKSTATUS_DIR = RAW_DIR / "dokumentstatus"
FAILED_PATH = DOKSTATUS_DIR / "_failed.json"

THROTTLE_S = 0.5  # ~2 req/s, polite to data.riksdagen.se


def run(retry_failed: bool = False) -> None:
    DOKSTATUS_DIR.mkdir(parents=True, exist_ok=True)
    dok_ids = (
        pl.scan_parquet(VOTES_PARQUET)
        .select(pl.col("dok_id").unique().sort())
        .collect()["dok_id"]
        .to_list()
    )
    failed: dict[str, str] = json.loads(FAILED_PATH.read_text()) if FAILED_PATH.exists() else {}

    todo = [
        d for d in dok_ids
        if not (DOKSTATUS_DIR / f"{d}.json").exists() and (retry_failed or d not in failed)
    ]
    print(f"{len(dok_ids)} dok_ids referenced, {len(todo)} to fetch")

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for i, dok_id in enumerate(todo, 1):
            url = DOKUMENTSTATUS_URL.format(dok_id=dok_id)
            try:
                r = client.get(url)
                r.raise_for_status()
                # The API returns text/html content-type but the body is JSON.
                data = json.loads(r.text.lstrip("﻿"))
                if "dokumentstatus" not in data:
                    raise ValueError("no dokumentstatus key in response")
                (DOKSTATUS_DIR / f"{dok_id}.json").write_text(
                    json.dumps(data, ensure_ascii=False)
                )
                failed.pop(dok_id, None)
            except Exception as e:  # noqa: BLE001 — record and continue
                print(f"  FAILED {dok_id}: {e}")
                failed[dok_id] = str(e)
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}")
            time.sleep(THROTTLE_S)

    FAILED_PATH.write_text(json.dumps(failed, ensure_ascii=False, indent=1))
    n_ok = len(list(DOKSTATUS_DIR.glob("*.json"))) - 1  # minus _failed.json
    print(f"done: {n_ok} dokumentstatus files, {len(failed)} failed")
