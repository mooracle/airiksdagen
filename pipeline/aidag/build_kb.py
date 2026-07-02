"""Build monthly point-in-time country-state snapshots (data/kb/snapshots/).

The no-future-information rule: every indicator carries a vintage_date (when
the value was publicly known) that must be <= the last day of the snapshot
month. Publication-lag rules:

- Policy rate (Riksbank SWEA, daily): known same day -> latest obs in month.
- KPI yoy (SCB TAB6596): month X published ~14th of X+1 -> snapshot M uses
  data for M-1 with vintage M-15 (approximation of SCB's calendar).
- Unemployment (SCB TAB6387, seasonally adjusted): month X published ~3.5
  weeks into X+1; we use M-2 with vintage 25th of M-1 to stay safely inside
  the rule even when publication slips.
- Events: Swedish Wikipedia "{year} i Sverige" chronology; events are dated
  within the month by construction. (Caveat, documented in methodology: the
  descriptions are written retrospectively.)
"""

from __future__ import annotations

import calendar
import json
import re

import httpx

from aidag.config import KB_DIR, PROCESSED_DIR

RIKSBANK_OBS_URL = "https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/{frm}/{to}"
SCB_KPI_URL = (
    "https://statistikdatabasen.scb.se/api/v2/tables/TAB6596/data"
    "?lang=sv&valueCodes%5BContentsCode%5D=00000804&valueCodes%5BTid%5D=*&outputFormat=json-stat2"
)
SCB_AKU_URL = (
    "https://statistikdatabasen.scb.se/api/v2/tables/TAB6387/data"
    "?lang=sv&valueCodes%5BArbetskraftstillh%5D=AL%C3%96SP&valueCodes%5BTypData%5D=SR_DATA"
    "&valueCodes%5BKon%5D=1%2B2&valueCodes%5BAlder%5D=tot15-74&valueCodes%5BContentsCode%5D=000007L9"
    "&valueCodes%5BTid%5D=*&outputFormat=json-stat2"
)
WIKI_API = "https://sv.wikipedia.org/w/api.php"

# Government timeline for the 2022–2026 period (used by promptgen per case date too).
GOVERNMENTS = [
    {
        "from": "2021-11-30",
        "statsminister": "Magdalena Andersson",
        "parti": "S",
        "koalition": ["S"],
        "stodpartier": [],
        "beskrivning": "Socialdemokratisk enpartiregering (expeditionsministär efter valet 2022-09-11).",
    },
    {
        "from": "2022-10-18",
        "statsminister": "Ulf Kristersson",
        "parti": "M",
        "koalition": ["M", "KD", "L"],
        "stodpartier": ["SD"],
        "beskrivning": "Regeringen Kristersson (M, KD, L) med stöd av SD enligt Tidöavtalet (2022-10-14).",
    },
]

SV_MONTHS = [
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
]

MAX_EVENTS_PER_MONTH = 8


def month_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def month_shift(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def government_at(date: str) -> dict:
    current = GOVERNMENTS[0]
    for g in GOVERNMENTS:
        if g["from"] <= date:
            current = g
    return current


def fetch_policy_rate(client: httpx.Client, first: str, last: str) -> dict[str, tuple[str, float]]:
    """date -> value for all business days; caller picks the latest per month."""
    r = client.get(RIKSBANK_OBS_URL.format(frm=f"{first}-01", to=month_end(last)))
    r.raise_for_status()
    return {o["date"]: o["value"] for o in r.json()}


def fetch_scb_series(client: httpx.Client, url: str) -> dict[str, float]:
    """json-stat2 -> {'YYYY-MM': value} (skips nulls)."""
    r = client.get(url)
    r.raise_for_status()
    d = r.json()
    tid_index = d["dimension"]["Tid"]["category"]["index"]
    values = d["value"]
    out = {}
    for tid, i in tid_index.items():
        if values[i] is not None:
            out[tid.replace("M", "-")] = float(values[i])
    return out


WIKI_LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
WIKI_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL)
WIKI_BOLD_RE = re.compile(r"'{2,}")


WIKI_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")


def clean_wikitext(text: str) -> str:
    text = WIKI_REF_RE.sub("", text)
    for _ in range(3):  # templates can nest
        text = WIKI_TEMPLATE_RE.sub("", text)
    text = WIKI_LINK_RE.sub(r"\1", text)
    text = WIKI_BOLD_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_year_events(client: httpx.Client, year: int) -> dict[str, list[dict]]:
    """Parse the sv.wikipedia year article ('2023') into {YYYY-MM: [events]}.

    Structure: '== Händelser ==' → '=== Januari ===' sections with bullets like
    '* [[19 januari]] – text', '* [[1 januari]]:' followed by '**' sub-bullets.
    We stop at the next top-level section (Avlidna etc.).
    """
    r = client.get(
        WIKI_API,
        params={"action": "parse", "page": str(year), "prop": "wikitext", "format": "json", "formatversion": 2},
    )
    r.raise_for_status()
    wikitext = r.json().get("parse", {}).get("wikitext", "")
    source_url = f"https://sv.wikipedia.org/wiki/{year}"

    events: dict[str, list[dict]] = {}
    in_handelser = False
    current_month = 0
    pending_day = 0
    date_re = re.compile(r"\[\[(\d{1,2})\s+(" + "|".join(SV_MONTHS) + r")(?:\|[^\]]*)?\]\]")

    def add(day: int, text: str) -> None:
        text = clean_wikitext(text)
        if len(text) < 15:
            return
        key = f"{year}-{current_month:02d}"
        events.setdefault(key, []).append({
            "date": f"{key}-{day:02d}",
            "text": text,
            "source_url": source_url,
        })

    for line in wikitext.splitlines():
        heading = re.match(r"^(=+)\s*(.+?)\s*=+\s*$", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip().lower()
            if level == 2:
                in_handelser = title == "händelser"
                current_month = 0
            elif level == 3 and in_handelser:
                current_month = SV_MONTHS.index(title) + 1 if title in SV_MONTHS else 0
            continue
        if not (in_handelser and current_month and line.startswith("*")):
            continue
        depth = len(line) - len(line.lstrip("*"))
        body = line.lstrip("*").strip()
        m = date_re.search(body)
        if depth == 1:
            if not m:
                continue
            pending_day = int(m.group(1))
            text = body[m.end():].lstrip(" :–—-")
            if text:
                add(pending_day, text)
        elif depth >= 2 and pending_day:
            add(pending_day, body)
    return events


def build_snapshot(
    month: str,
    policy_rates: dict[str, float],
    kpi: dict[str, float],
    aku: dict[str, float],
    events: dict[str, list[dict]],
) -> dict:
    end = month_end(month)
    indicators = []

    rate_dates = [d for d in policy_rates if d <= end]
    if rate_dates:
        last = max(rate_dates)
        indicators.append({
            "series": "SECBREPOEFF", "label": "Riksbankens styrränta",
            "value": policy_rates[last], "unit": "%", "period": last,
            "vintage_date": last,
            "source_url": "https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF",
        })

    kpi_month = month_shift(month, -1)
    if kpi_month in kpi:
        indicators.append({
            "series": "KPI-yoy", "label": "Inflation (KPI, årstakt)",
            "value": kpi[kpi_month], "unit": "%", "period": kpi_month,
            "vintage_date": f"{month}-15",
            "source_url": "https://statistikdatabasen.scb.se/api/v2/tables/TAB6596",
        })

    aku_month = month_shift(month, -2)
    if aku_month in aku:
        indicators.append({
            "series": "AKU-arbetslöshet", "label": "Arbetslöshet (säsongrensad, 15–74 år)",
            "value": aku[aku_month], "unit": "%", "period": aku_month,
            "vintage_date": f"{month_shift(month, -1)}-25",
            "source_url": "https://statistikdatabasen.scb.se/api/v2/tables/TAB6387",
        })

    gov = government_at(f"{month}-01")
    return {
        "month": month,
        "government": {k: v for k, v in gov.items() if k != "from"} | {"sedan": gov["from"]},
        "indicators": indicators,
        "events": (events.get(month) or [])[:MAX_EVENTS_PER_MONTH],
    }


def snapshot_months() -> list[str]:
    cases_path = PROCESSED_DIR / "cases.parquet"
    if cases_path.exists():
        import polars as pl

        return sorted(pl.read_parquet(cases_path)["kb_month"].unique().to_list())
    return [month_shift("2022-10", i) for i in range(45)]


def run(month: str | None = None, force: bool = False) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    months = [month] if month else snapshot_months()
    todo = [m for m in months if force or not (KB_DIR / f"{m}.json").exists()]
    if not todo:
        print("all snapshots exist, nothing to do")
        return

    headers = {"User-Agent": "aidag-research/0.1 (open research project on Riksdag votes)"}
    with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as client:
        print("fetching Riksbank policy rate…")
        policy = fetch_policy_rate(client, min(todo), max(todo))
        print("fetching SCB KPI + AKU…")
        kpi = fetch_scb_series(client, SCB_KPI_URL)
        aku = fetch_scb_series(client, SCB_AKU_URL)
        years = sorted({int(m[:4]) for m in todo})
        events: dict[str, list[dict]] = {}
        for y in years:
            print(f"fetching Wikipedia events {y}…")
            events.update(fetch_year_events(client, y))

    for m in todo:
        snap = build_snapshot(m, policy, kpi, aku, events)
        (KB_DIR / f"{m}.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1))
        print(f"  {m}: {len(snap['indicators'])} indicators, {len(snap['events'])} events")
    print(f"done: {len(todo)} snapshots")
