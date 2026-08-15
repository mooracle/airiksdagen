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
Mark 1.0, open access, redistribution permitted. SND publishes each 2022
valmanifest in two renditions, and we use both:

`https://snd.se/sv/vivill/file/{s,m,sd,c,v,kd,mp,l}/v/2022/pdf`  (7 of 8)
`https://snd.se/sv/vivill/file/{c}/v/2022/txt`                   (valmanifest-2022-c only)

Seven of the eight committed `.txt` files are now derived from the PDF rendition,
because that is the one carrying the font metrics and page geometry the structured
extraction reads. `valmanifest-2022-c`'s PDF has no text layer, so it keeps the
plain-text rendition; its block file records `"source": "text"` and the others
`"source": "pdf"`. The swap is made where the corpus is regenerated, behind a
word-count equivalence guard (`extract_corpus.check_equivalence`) that refuses two
renditions that are not the same document — never as a side effect of fetching.

Party programmes (partiprogram / principprogram / idéprogram), pinned to the
version standing at the 2022 election: 15 PDFs from the parties' own sites, listed
per party in `config.PARTY_PROGRAMS`. These are the parties' own published
platforms, redistributed here for research citation; unlike the SND manifestos they
carry no Public Domain Mark.

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
- **Committed, and the reason the corpus is reproducible**: `data/corpus/pdf/` —
  the 23 source PDFs (24 MB) behind the re-extracted documents, 8 SND manifestos
  and 15 party-site programmes. Extraction reads this cache and never the network
  (`fetch_corpus.source_pdf_bytes` raises rather than downloading), because the 15
  programme URLs are live party-site links and every one of those parties has
  replaced the pinned edition at least once. Without the cache a re-extraction
  would quietly build against a different edition than the decisions were.
- **Gitignored, re-fetchable**: `data/raw/`, `data/processed/` — rebuild with
  `aidag fetch-votes && aidag fetch-cases && aidag build-cases`.
