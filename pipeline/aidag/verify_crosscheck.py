"""Cross-check our vote aggregation against the Riksdag's own per-party tables.

dokumentstatus embeds `votering_sammanfattning_html` per förslagspunkt — the
official per-party Ja/Nej/Avstår/Frånvarande table. Any mismatch with our
aggregation from per-MP rows means a parser bug, not a data problem.
"""

from __future__ import annotations

import json

import polars as pl

from aidag.build_cases import _as_list, extract_forslag, load_dokstatus


def parse_summary_table(uf: dict) -> dict[str, tuple[int, int, int, int]] | None:
    """utskottsforslag -> {parti: (ja, nej, avstår, frånvarande)} or None."""
    wrapper = uf.get("votering_sammanfattning_html")
    if not isinstance(wrapper, dict):
        return None
    tables = _as_list(wrapper.get("table"))
    if not tables:
        return None
    # A punkt can carry several tables (sakfrågan + motivfrågan); we aggregate
    # sakfråga votes, so pick the table whose caption says "sakfrågan".
    table = None
    for cand in tables:
        caption = cand.get("caption") if isinstance(cand, dict) else None
        caption_text = json.dumps(caption, ensure_ascii=False) if caption else ""
        if "sakfrågan" in caption_text:
            table = cand
            break
    if table is None:
        table = tables[0] if isinstance(tables[0], dict) else None
    if table is None:
        return None
    tbody = table.get("tbody", {})
    out: dict[str, tuple[int, int, int, int]] = {}
    for tr in _as_list(tbody.get("tr")):
        parti = str(tr.get("th") or "").strip().upper()
        tds = _as_list(tr.get("td"))
        if parti in {"TOTALT", ""} or len(tds) < 4:
            continue
        try:
            out[parti] = tuple(int(str(td).strip()) for td in tds[:4])  # type: ignore[assignment]
        except (ValueError, TypeError):
            return None
    return out or None


def crosscheck_positions(cases: pl.DataFrame, positions: pl.DataFrame) -> tuple[int, int]:
    """Returns (n voteringar checked, n with any party-count mismatch)."""
    pos_lookup = {
        (r["votering_id"], r["parti"]): (r["n_ja"], r["n_nej"], r["n_avstar"], r["n_franvarande"])
        for r in positions.iter_rows(named=True)
    }
    n_checked = 0
    n_mismatch = 0
    ds_cache: dict[str, dict | None] = {}
    for row in cases.select("votering_id", "dok_id").iter_rows(named=True):
        dok_id = row["dok_id"]
        if dok_id not in ds_cache:
            ds_cache[dok_id] = load_dokstatus(dok_id)
        ds = ds_cache[dok_id]
        if ds is None:
            continue
        uf = extract_forslag(ds).get(row["votering_id"])
        if uf is None:
            continue
        official = parse_summary_table(uf)
        if official is None:
            continue
        n_checked += 1
        for parti, counts in official.items():
            ours = pos_lookup.get((row["votering_id"], parti))
            if ours is not None and tuple(ours) != counts:
                n_mismatch += 1
                print(f"    mismatch {row['votering_id']} {parti}: ours={ours} official={counts}")
                break
    return n_checked, n_mismatch
