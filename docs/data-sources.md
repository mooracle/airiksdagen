# Data sources

Every external source used by the pipeline, with endpoints and licensing.

## Riksdag votes and documents — data.riksdagen.se

**Attribution (required): Källa: Sveriges riksdag.** Data is free to use and
redistribute per the [terms of use](https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/anvandningsvillkor/).
No API key, no documented rate limits — we throttle to ~2 req/s and prefer bulk
dumps. Per-MP rows contain personal data (names, birth year); we publish only
what the Riksdag itself publishes.

| Use | Endpoint |
|---|---|
| Bulk votes, one zip per riksmöte (per-MP rows) | `https://data.riksdagen.se/dataset/votering/votering-{202223,202324,202425,202526}.json.zip` |
| Case content: förslagspunkter, reservations, per-party result table | `https://data.riksdagen.se/dokumentstatus/{dok_id}.json` |
| Document lists (pagination via `@nasta_sida`) | `https://data.riksdagen.se/dokumentlista/?doktyp=bet&rm=2023%2F24&utformat=json` |
| Single votering by UUID | `https://data.riksdagen.se/votering/{votering_id}/json` |

Vote rows are filtered to `avser=sakfrågan`, `votering=huvud` (main votes on the
substantive question).

## Party documents — SND Vivill

[Svensk Nationell Datatjänst, Vivill](https://snd.se/sv/vivill) — Public Domain
Mark 1.0, open access, redistribution permitted. We commit the plain-text 2022
valmanifest for all eight parties:

`https://snd.se/sv/vivill/file/{s,m,sd,c,v,kd,mp,l}/v/2022/txt`

Tidöavtalet (2022-10-14): PDF published by the four cooperating parties,
`https://www.liberalerna.se/wp-content/uploads/tidoavtalet-overenskommelse-for-sverige-slutlig.pdf`
(converted to text in `data/corpus/`).

## Country-state snapshots (point-in-time)

| Source | Use | Terms |
|---|---|---|
| [Riksbank SWEA API](https://api.riksbank.se/swea/v1/) | Policy rate (`SECBREPOEFF`), daily history | Open data, attribution |
| [Riksbank forecasts & outcomes](https://www.riksbank.se/en-gb/statistics/macro-indicators/forecasts-and-outcomes/) | CPI/GDP/unemployment *as known at each date* (true vintages) | Open data, attribution |
| [SCB PxWebApi 2](https://statistikdatabasen.scb.se/api/v2/) | KPI and labour-market series | CC0-style open data, attribution |
| Wikipedia (sv/en) via MediaWiki API | Monthly event digests, fetched as revision-at-date | CC BY-SA 4.0 |
| [SwedishPolls](https://github.com/MansMeg/SwedishPolls) (curated dataset of published Swedish opinion polls) | Party support per month (average of polls published that month; vintage = latest publication date used) | Open dataset compiled from publicly released polls; attribute the repo |

The no-future-information rule: every indicator in a monthly snapshot carries a
`vintage_date` that must fall on or before the end of that month; `aidag verify kb`
enforces this.

## What is committed vs. gitignored

- **Committed**: `data/corpus/` (manifesto texts, PDM), `data/kb/snapshots/`,
  `data/results/` (simulation outputs — the scientific record).
- **Gitignored, re-fetchable**: `data/raw/`, `data/processed/` — rebuild with
  `aidag fetch-votes && aidag fetch-cases && aidag build-cases`.
