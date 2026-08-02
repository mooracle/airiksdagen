# Worldstate plan: per-date news/economy context as referenced input

Status: **planned** (implement before starting full-v1; becomes prompt p4).

## Why

Parties write their plans before the election; reality then moves. A vote in
March 2023 happens under 12% food inflation, an energy crisis and a war —
dynamics no manifesto anticipated. Today's knowledge base is a thin monthly
snapshot (3 indicators + a handful of Wikipedia lines). This plan upgrades it
to a **per-vote-date worldstate** that the agent receives as incoming data and
must **explicitly reference when — and only when — it affects the vote**.

The research question does not change: plan fidelity remains the normative
basis. Worldstate exists to (a) make decisions on crisis-era and
`not_covered` cases realistic, and (b) create a *measurable dimension*: how
often does the document-faithful line get modulated by events, per party, per
period?

## Decision-model change (p4)

Add a structured block to the decision schema:

```json
"omvarld": {
  "paverkar": false,          // did worldstate materially affect this decision?
  "faktorer": [               // only when paverkar=true, max 3
    { "faktor": "hög inflation", "effekt": "stärker dokumentens linje om utgiftsdisciplin" }
  ]
}
```

Prompt rules (added to the role prompt):

- The party's documents remain the basis for the vote. Worldstate may be
  referenced only when it materially affects how the documents apply
  (crisis measures, situations the plan never anticipated).
- If worldstate is decisive because the documents are silent, set
  `coverage="not_covered"` and `omvarld.paverkar=true` — this separates
  "followed the plan", "extrapolated the plan" and "plan had no answer,
  world decided" in the aggregates.

Aggregation gains: share of worldstate-affected decisions per party/month
(expect spikes 2022-10 → 2023-06); drift vs worldstate-affected correlation
("do parties deviate from plans more in crisis months?").

## Data architecture

```
data/raw/worldstate/          gitignored per-source fetches
data/worldstate/
  indicators.parquet          series, period, value, vintage_date, source_url
  events.parquet              date, text, source, weight, source_url
```

New commands: `aidag build-worldstate` (fetch + compile), `aidag verify
worldstate` (vintage rule per row). The compiled parquet files are committed
(a few MB) for reproducibility.

At prompt render time, `worldstate_for(case_date)` assembles:

- **Indicators**: latest value whose `vintage_date < case_date` (strict
  point-in-time, per date instead of per month).
- **Events**: items from the ~30 days before `case_date`, ranked by source
  weight, capped at ~10. Presented with coarse recency ("senaste veckorna"),
  never exact dates; the standard identifier/date scrubbing applies.

The existing monthly KB stays for the website; the worldstate layer feeds
prompts and the site's case pages ("Omvärldsläge som vägdes in").

## Datasets (v1 → v2)

### Phase A — core (implement first)

| Source | What | Cadence | Access/license |
|---|---|---|---|
| Riksbank SWEA (`api.riksbank.se/swea/v1`) | policy rate (have) + **SEK/EUR, SEK/USD daily FX** | daily | open, attribution |
| SCB PxWebApi 2 | KPI/KPIF yoy (have), AKU (have), **BNP-indikator (monthly GDP proxy)**, quarterly GDP | monthly/quarterly, vintage-lagged | open (CC0-style) |
| Konjunkturinstitutet (`statistik.konj.se`) | **Barometerindikatorn** — economy-wide sentiment, strong crisis signal | monthly | open API |
| Riksdag `dokumentlista` (doktyp=ip, fr) | **titles of interpellations & written questions** — parliament's own issue agenda per week; superb salience signal, same license we already use | daily | free, "Källa: Sveriges riksdag" |
| en.wikipedia `Portal:Current_events/<date>` | structured **daily world events** (much richer than the sv year article we use now) | daily | CC BY-SA 4.0 |
| sv.wikipedia year articles | Swedish events (have) | monthly | CC BY-SA 4.0 |

### Phase B — high-salience additions

| Source | What | Notes |
|---|---|---|
| ENTSO-E Transparency / Svenska kraftnät (Mimer) | **day-ahead electricity prices SE1–SE4** — the political story of 2022–23 | free with registration; open reuse |
| Migrationsverket statistics | monthly asylum applications / residence permits | published monthly tables (xlsx); central to migration votes |
| Krisinformation.se API | official crisis notices | open |
| (flagged) Sveriges Radio öppna API | Ekot news headlines per day | metadata-only use; run a licensing check before committing texts |
| (flagged) GDELT | machine-coded world news signal | open but noisy/huge; only if Phase A events prove too thin |

Deliberately excluded: SVT/DN/commercial news text (copyright), opinion polls
(already excluded by design — agents must not adapt to opinion).

## Leakage & contamination controls (extended)

- `verify worldstate`: every indicator `vintage_date < case_date`; every event
  `date < case_date`; regression tests extend the golden prompt suite.
- Event text passes the existing scrub (doc numbers, ISO dates, author
  clauses) plus removal of any "riksdagen beslutade/röstade" phrasing —
  an event describing a *previous* vote outcome is legitimate history, but an
  event describing *this* case would be a leak; filter events mentioning the
  case's own beteckning-free rubrik tokens (conservative string check, logged).
- Richer context = richer recall triggers, so the leakage scrub above is the
  load-bearing contamination defence and its golden tests must extend to cover
  every new worldstate field.

## Implementation order

1. `worldstate.py` (fetchers + compile + `worldstate_for(date)`) and
   `build-worldstate`/`verify worldstate` commands — Phase A sources.
2. Schema/prompt p4 (`omvarld` block) in `promptgen.py`, `models.py`,
   `scripts/agent_batch_workflow.js`; golden tests; regenerate full-v1
   batch manifests.
3. Aggregates: worldstate-affected share per party/month; site: case-page
   "Omvärldsläge" panel + drift-vs-crisis overlay on the homepage chart.
4. Pilot re-validation: re-run the 10-case agent pilot on p4, diff against
   p1 decisions (expect: same rost in the clear-covered cases; better
   grounding on crisis budget cases), then start full-v1.
5. Phase B sources as a follow-up, no schema change needed.

Estimated effort: Phase A + schema + tests ≈ one working session; Phase B
another. Zero API cost until the p4 pilot re-validation (~90 subagents).
