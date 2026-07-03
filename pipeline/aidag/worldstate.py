"""Per-date worldstate: economy + news context known before each vote date.

See docs/worldstate-plan.md. Two compiled datasets (committed):

  data/worldstate/indicators.parquet
      series, label, unit, period, value, vintage_date, source_url
  data/worldstate/events.parquet
      date, text, source, weight, source_url

`worldstate_for(case_date)` selects strictly point-in-time rows
(vintage_date < case_date, event date < case_date). Publication lags for
monthly series are approximated conservatively and documented per fetcher.
"""

from __future__ import annotations

import calendar
import json
import re
import time
from datetime import date, timedelta

import httpx
import polars as pl

from aidag.config import DATA_DIR, RAW_DIR, RIKSMOTEN

WS_DIR = DATA_DIR / "worldstate"
WS_RAW = RAW_DIR / "worldstate"
INDICATORS_PARQUET = WS_DIR / "indicators.parquet"
EVENTS_PARQUET = WS_DIR / "events.parquet"

HEADERS = {"User-Agent": "aidag-research/0.1 (open research project on Riksdag votes)"}

START = date(2022, 9, 1)


def _end() -> date:
    import polars as pl

    from aidag.config import PROCESSED_DIR

    cases = PROCESSED_DIR / "cases.parquet"
    if cases.exists():
        last = pl.read_parquet(cases)["datum"].max()
        return date.fromisoformat(last)
    return date(2026, 6, 30)


def _month_range(a: date, b: date) -> list[str]:
    out = []
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _month_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _shift_month(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + m - 1 + delta
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


# ---------------------------------------------------------------- indicators


def fetch_fx(client: httpx.Client, end: date) -> list[dict]:
    """Riksbank SWEA daily mid rates. Known same day -> vintage = date."""
    rows = []
    for series, label in [("SEKEURPMI", "Växelkurs EUR/SEK"), ("SEKUSDPMI", "Växelkurs USD/SEK")]:
        r = client.get(
            f"https://api.riksbank.se/swea/v1/Observations/{series}/{START}/{end}"
        )
        r.raise_for_status()
        for o in r.json():
            rows.append({
                "series": series, "label": label, "unit": "SEK",
                "period": o["date"], "value": float(o["value"]),
                "vintage_date": o["date"],
                "source_url": "https://api.riksbank.se/swea/v1",
            })
    return rows


def fetch_policy_rate(client: httpx.Client, end: date) -> list[dict]:
    r = client.get(f"https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/{START}/{end}")
    r.raise_for_status()
    return [
        {
            "series": "SECBREPOEFF", "label": "Riksbankens styrränta", "unit": "%",
            "period": o["date"], "value": float(o["value"]), "vintage_date": o["date"],
            "source_url": "https://api.riksbank.se/swea/v1",
        }
        for o in r.json()
    ]


def _scb_series(client: httpx.Client, url: str) -> dict[str, float]:
    r = client.get(url)
    r.raise_for_status()
    d = r.json()
    tid = d["dimension"]["Tid"]["category"]["index"]
    return {
        t.replace("M", "-"): float(d["value"][i])
        for t, i in tid.items()
        if d["value"][i] is not None
    }


def fetch_scb(client: httpx.Client) -> list[dict]:
    """KPI yoy (publ ~15th of M+1), AKU unemployment (publ ~25th of M+1),
    BNP-indikator yoy from index TAB443 (publ ~ end of M+2, conservative)."""
    rows = []
    kpi = _scb_series(
        client,
        "https://statistikdatabasen.scb.se/api/v2/tables/TAB6596/data"
        "?lang=sv&valueCodes%5BContentsCode%5D=00000804&valueCodes%5BTid%5D=*&outputFormat=json-stat2",
    )
    for period, value in kpi.items():
        rows.append({
            "series": "KPI-yoy", "label": "Inflation (KPI, årstakt)", "unit": "%",
            "period": period, "value": value,
            "vintage_date": f"{_shift_month(period, 1)}-15",
            "source_url": "https://statistikdatabasen.scb.se/api/v2/tables/TAB6596",
        })
    aku = _scb_series(
        client,
        "https://statistikdatabasen.scb.se/api/v2/tables/TAB6387/data"
        "?lang=sv&valueCodes%5BArbetskraftstillh%5D=AL%C3%96SP&valueCodes%5BTypData%5D=SR_DATA"
        "&valueCodes%5BKon%5D=1%2B2&valueCodes%5BAlder%5D=tot15-74&valueCodes%5BContentsCode%5D=000007L9"
        "&valueCodes%5BTid%5D=*&outputFormat=json-stat2",
    )
    for period, value in aku.items():
        rows.append({
            "series": "AKU", "label": "Arbetslöshet (säsongrensad)", "unit": "%",
            "period": period, "value": value,
            "vintage_date": f"{_shift_month(period, 1)}-25",
            "source_url": "https://statistikdatabasen.scb.se/api/v2/tables/TAB6387",
        })
    # ContentsCode 0000027O = calendar-adjusted yoy volume change, %
    bnp = _scb_series(
        client,
        "https://statistikdatabasen.scb.se/api/v2/tables/TAB443/data"
        "?lang=sv&valueCodes%5BBNPMarknadspris%5D=BNPM&valueCodes%5BContentsCode%5D=0000027O"
        "&valueCodes%5BTid%5D=*&outputFormat=json-stat2",
    )
    for period, value in bnp.items():
        rows.append({
            "series": "BNP-ind-yoy", "label": "BNP-indikator (årstakt)", "unit": "%",
            "period": period, "value": value,
            "vintage_date": _month_end(_shift_month(period, 2)),
            "source_url": "https://statistikdatabasen.scb.se/api/v2/tables/TAB443",
        })
    return rows


def fetch_ki_barometer(client: httpx.Client) -> list[dict]:
    """Konjunkturinstitutets Barometerindikatorn: published ~end of the same
    month -> vintage = 28th of the period month."""
    r = client.post(
        "https://statistik.konj.se/PxWeb/api/v1/sv/KonjBar/indikatorer/Indikatorm.px",
        json={
            "query": [{"code": "Indikator", "selection": {"filter": "item", "values": ["BTOT"]}}],
            "response": {"format": "json"},
        },
    )
    r.raise_for_status()
    rows = []
    for item in r.json()["data"]:
        period = item["key"][1].replace("M", "-")  # key = [Indikator, Period]
        try:
            value = float(item["values"][0])
        except (ValueError, IndexError):
            continue
        rows.append({
            "series": "KI-BTOT", "label": "Konjunkturbarometern (100 = normalläge)", "unit": "index",
            "period": period, "value": value,
            "vintage_date": f"{period}-28",
            "source_url": "https://statistik.konj.se",
        })
    return rows


def fetch_asylum(client: httpx.Client) -> list[dict]:
    """Eurostat monthly asylum applications, Sweden. Publication lag ~2 months."""
    r = client.get(
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/migr_asyappctzm"
        "?format=JSON&geo=SE&citizen=TOTAL&sex=T&age=TOTAL&applicant=TOTAL&unit=PER"
    )
    r.raise_for_status()
    d = r.json()
    tid = d["dimension"]["time"]["category"]["index"]
    rows = []
    for period, i in tid.items():
        value = d["value"].get(str(i))
        if value is None:
            continue
        rows.append({
            "series": "ASYL", "label": "Asylansökningar (månad)", "unit": "st",
            "period": period, "value": float(value),
            "vintage_date": f"{_shift_month(period, 2)}-15",
            "source_url": "https://ec.europa.eu/eurostat/databrowser/product/view/migr_asyappctzm",
        })
    return rows


def fetch_elpriser(client: httpx.Client, end: date) -> list[dict]:
    """Day-ahead electricity price SE3 (elprisetjustnu.se, from 2022-11-01).
    Daily mean of hourly SEK/kWh; known the day before -> vintage = date."""
    cache = WS_RAW / "elpriser-se3.json"
    cached: dict[str, float] = json.loads(cache.read_text()) if cache.exists() else {}
    d = max(date(2022, 11, 1), START)
    while d <= end:
        key = d.isoformat()
        if key not in cached:
            url = f"https://www.elprisetjustnu.se/api/v1/prices/{d.year}/{d.month:02d}-{d.day:02d}_SE3.json"
            try:
                r = client.get(url)
                if r.status_code == 200:
                    hours = r.json()
                    cached[key] = round(sum(h["SEK_per_kWh"] for h in hours) / len(hours), 4)
                else:
                    cached[key] = float("nan")
            except httpx.HTTPError:
                cached[key] = float("nan")
            time.sleep(0.05)
            if len(cached) % 200 == 0:
                cache.write_text(json.dumps(cached))
                print(f"    elpriser: {len(cached)} days")
        d += timedelta(days=1)
    cache.write_text(json.dumps(cached))
    return [
        {
            "series": "ELPRIS-SE3", "label": "Elpris SE3 (dygnsmedel)", "unit": "kr/kWh",
            "period": k, "value": v, "vintage_date": k,
            "source_url": "https://www.elprisetjustnu.se",
        }
        for k, v in cached.items()
        if v == v  # drop NaN days
    ]


# ------------------------------------------------------------------- events

SV_MONTHS_EN = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(fragment: str) -> str:
    import html

    return html.unescape(TAG_RE.sub("", fragment)).strip()


def fetch_wiki_daily_events(client: httpx.Client, end: date) -> list[dict]:
    """en.wikipedia Portal:Current events, rendered monthly archive pages.
    Top-level <li> items per day, capped, CC BY-SA."""
    rows = []
    for month in _month_range(START, end):
        y, m = int(month[:4]), int(month[5:7])
        page = f"Portal:Current events/{SV_MONTHS_EN[m - 1]} {y}"
        r = client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "parse", "page": page, "prop": "text", "format": "json", "formatversion": 2},
        )
        if r.status_code != 200 or "parse" not in r.json():
            continue
        html_text = r.json()["parse"]["text"]
        # day blocks carry ids like "2023_March_15"
        parts = re.split(r'id="(\d{4})_(\w+)_(\d{1,2})"', html_text)
        for i in range(1, len(parts) - 3, 4):
            yy, mon_name, dd = parts[i], parts[i + 1], int(parts[i + 2])
            if mon_name not in SV_MONTHS_EN:
                continue
            day = f"{yy}-{SV_MONTHS_EN.index(mon_name) + 1:02d}-{dd:02d}"
            block = parts[i + 3]
            items = re.findall(r"<li>(?!<)(.*?)</li>", block, flags=re.DOTALL)
            n = 0
            for item in items:
                text = _strip_html(item)
                if len(text) < 40 or text.count("\n") > 2:
                    continue
                rows.append({
                    "date": day, "text": " ".join(text.split())[:400],
                    "source": "wikipedia-en", "weight": 2,
                    "source_url": f"https://en.wikipedia.org/wiki/{page.replace(' ', '_')}",
                })
                n += 1
                if n == 6:
                    break
        time.sleep(0.2)
    return rows


def fetch_riksdag_questions(client: httpx.Client) -> list[dict]:
    """Titles of interpellations + written questions — parliament's own issue
    agenda, per day. Same license as the vote data."""
    rows = []
    for doktyp, prefix in [("ip", "Interpellation"), ("fr", "Skriftlig fråga")]:
        for rm in RIKSMOTEN:
            url = (
                f"https://data.riksdagen.se/dokumentlista/?doktyp={doktyp}"
                f"&rm={rm.replace('/', '%2F')}&sz=200&utformat=json&sort=datum&sortorder=asc"
            )
            while url:
                r = client.get(url)
                r.raise_for_status()
                data = json.loads(r.text.lstrip("﻿"))["dokumentlista"]
                for dok in data.get("dokument", []) if isinstance(data.get("dokument"), list) else []:
                    titel = (dok.get("titel") or "").strip()
                    datum = (dok.get("datum") or "")[:10]
                    if titel and datum:
                        rows.append({
                            "date": datum, "text": f"{prefix} i riksdagen: {titel}",
                            "source": "riksdag-fragor", "weight": 4,
                            "source_url": f"https://data.riksdagen.se/dokument/{dok.get('id', '')}",
                        })
                url = data.get("@nasta_sida") or None
                time.sleep(0.3)
    return rows


def fetch_krisinformation(client: httpx.Client) -> list[dict]:
    r = client.get("https://api.krisinformation.se/v3/news?days=2000")
    r.raise_for_status()
    rows = []
    for item in r.json():
        text = (item.get("PushMessage") or item.get("Headline") or "").strip()
        published = (item.get("Published") or "")[:10]
        if text and published >= START.isoformat():
            rows.append({
                "date": published, "text": f"Krisinformation: {text}"[:400],
                "source": "krisinformation", "weight": 5,
                "source_url": item.get("Web") or "https://www.krisinformation.se",
            })
    return rows


def fetch_sv_wiki_events(client: httpx.Client, end: date) -> list[dict]:
    """Reuse the sv.wikipedia year-article parser from build_kb (Swedish events)."""
    from aidag.build_kb import fetch_year_events

    rows = []
    for year in range(START.year, end.year + 1):
        for month_events in fetch_year_events(client, year).values():
            for e in month_events:
                rows.append({
                    "date": e["date"], "text": e["text"][:400],
                    "source": "wikipedia-sv", "weight": 3,
                    "source_url": e["source_url"],
                })
    return rows


# ------------------------------------------------------------------ compile


def run(force: bool = False) -> None:
    WS_DIR.mkdir(parents=True, exist_ok=True)
    WS_RAW.mkdir(parents=True, exist_ok=True)
    end = _end()
    with httpx.Client(timeout=120, follow_redirects=True, headers=HEADERS) as client:
        print("indicators: Riksbank policy + FX…")
        indicators = fetch_policy_rate(client, end) + fetch_fx(client, end)
        print("indicators: SCB (KPI, AKU, BNP-indikator)…")
        indicators += fetch_scb(client)
        print("indicators: KI barometer…")
        indicators += fetch_ki_barometer(client)
        print("indicators: Eurostat asylum…")
        indicators += fetch_asylum(client)
        print("indicators: elpriser SE3 (daily, cached)…")
        indicators += fetch_elpriser(client, end)

        print("events: riksdag ip/fr titles…")
        events = fetch_riksdag_questions(client)
        print("events: krisinformation…")
        events += fetch_krisinformation(client)
        print("events: sv.wikipedia year articles…")
        events += fetch_sv_wiki_events(client, end)
        print("events: en.wikipedia daily current events…")
        events += fetch_wiki_daily_events(client, end)

    ind_df = pl.DataFrame(indicators).sort("series", "period")
    ev_df = (
        pl.DataFrame(events)
        .filter(pl.col("date") <= end.isoformat())
        .unique(subset=["date", "text"])
        .sort("date")
    )
    ind_df.write_parquet(INDICATORS_PARQUET)
    ev_df.write_parquet(EVENTS_PARQUET)
    print(f"worldstate: {len(ind_df)} indicator rows, {len(ev_df)} events -> {WS_DIR}")


# ------------------------------------------------------------------- lookup

_IND_CACHE: pl.DataFrame | None = None
_EV_CACHE: pl.DataFrame | None = None

# events describing chamber decisions could leak this case's own outcome
OUTCOME_RE = re.compile(r"[Rr]iksdagen\s+(beslut|röst|sa\s+ja|sa\s+nej|bifall|avslog)")

PROMPT_SERIES = [
    "SECBREPOEFF", "KPI-yoy", "AKU", "BNP-ind-yoy", "KI-BTOT",
    "SEKEURPMI", "ELPRIS-SE3", "ASYL",
]


def available() -> bool:
    return INDICATORS_PARQUET.exists() and EVENTS_PARQUET.exists()


def worldstate_for(case_date: str, max_events: int = 10) -> dict | None:
    """Point-in-time snapshot for one vote date, or None if not built."""
    global _IND_CACHE, _EV_CACHE
    if not available():
        return None
    if _IND_CACHE is None:
        _IND_CACHE = pl.read_parquet(INDICATORS_PARQUET)
        _EV_CACHE = pl.read_parquet(EVENTS_PARQUET)

    indicators = []
    known = _IND_CACHE.filter(pl.col("vintage_date") < case_date)
    for series in PROMPT_SERIES:
        sub = known.filter(pl.col("series") == series)
        if len(sub) == 0:
            continue
        if series == "ELPRIS-SE3":  # 30-day mean is more meaningful than one day
            window = sub.filter(
                pl.col("period") >= (date.fromisoformat(case_date) - timedelta(days=30)).isoformat()
            )
            if len(window) == 0:
                continue
            row = window.sort("period").row(-1, named=True)
            indicators.append({**row, "value": round(float(window["value"].mean()), 2),
                               "label": "Elpris SE3 (30-dagarssnitt)"})
        else:
            indicators.append(sub.sort("vintage_date").row(-1, named=True))

    frm = (date.fromisoformat(case_date) - timedelta(days=30)).isoformat()
    candidates = (
        _EV_CACHE.filter(
            (pl.col("date") < case_date)
            & (pl.col("date") >= frm)
            & ~pl.col("text").str.contains(OUTCOME_RE.pattern)
        )
        .sort(["weight", "date"], descending=[True, True])
        .to_dicts()
    )
    # source diversity: parliament questions must not crowd out world events
    per_source_cap = {"riksdag-fragor": 4}
    counts: dict[str, int] = {}
    events = []
    for e in candidates:
        cap = per_source_cap.get(e["source"], max_events)
        if counts.get(e["source"], 0) >= cap:
            continue
        counts[e["source"]] = counts.get(e["source"], 0) + 1
        events.append(e)
        if len(events) == max_events:
            break
    return {"date": case_date, "indicators": indicators, "events": events}


def verify() -> tuple[int, list[str]]:
    """(n rows checked, failures) for `aidag verify worldstate`."""
    failures = []
    if not available():
        return 0, ["worldstate parquet files missing — run `aidag build-worldstate`"]
    ind = pl.read_parquet(INDICATORS_PARQUET)
    ev = pl.read_parquet(EVENTS_PARQUET)
    bad_vintage = ind.filter(pl.col("vintage_date") < pl.col("period").str.slice(0, 10))
    # monthly rows: vintage may be within the period month only for same-day series
    same_day = {"SECBREPOEFF", "SEKEURPMI", "SEKUSDPMI", "ELPRIS-SE3"}
    bad = bad_vintage.filter(~pl.col("series").is_in(same_day))
    if len(bad) > 0:
        failures.append(f"{len(bad)} indicator rows with vintage before period")
    if ev.filter(pl.col("date").str.len_chars() != 10).height > 0:
        failures.append("events with malformed dates")
    for series in PROMPT_SERIES:
        if ind.filter(pl.col("series") == series).height == 0:
            failures.append(f"series {series} empty")
    return len(ind) + len(ev), failures
