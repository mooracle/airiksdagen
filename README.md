# AI Riksdag — vad partierna *borde* ha röstat enligt sina egna dokument

> **English summary below.**

Ett öppet forskningsprojekt: för varje votering i Sveriges riksdag under mandatperioden
2022–2026 låter vi en AI-agent per parti (S, M, SD, C, V, KD, MP, L) avgöra hur partiet
*borde* rösta — **enbart** utifrån partiets egna dokument före valet 2022 (valmanifest,
partiprogram, samt Tidöavtalet för regeringssidan) och en tidsbunden lägesbild av landet.
Agenten får ingen information daterad efter beslutsdagen.

Resultatet jämförs med hur partiet faktiskt röstade och publiceras som en statisk webbplats
med full statistik, källhänvisningar och en visualisering av kammarens 349 platser per ärende.

**Detta är rekonstruktion, inte prediktion.** Modellens träningsdata innehåller sannolikt de
verkliga utfallen. Vi mäter denna kontaminering med minnesprober och publicerar resultaten
öppet. Projektet är partipolitiskt obundet; metodik, prompts, kod och rådata är öppna.

## English summary

An open research project: for every chamber vote in the Swedish Riksdag 2022–2026, one AI
agent per party decides how that party *should* vote based solely on the party's own
pre-election documents plus a point-in-time snapshot of the country — no information after
the decision date. AI decisions are compared against actual party votes and published as a
static website with full statistics, per-case 349-seat chamber visualizations, and source
references. Framed as **reconstruction, not prediction** — training-data contamination is
acknowledged, measured with memorization probes, and reported. Not affiliated with any party.

## Pipeline

```
aidag fetch-votes    # bulk vote dumps → votes.parquet          (data.riksdagen.se)
aidag fetch-cases    # dokumentstatus per case                  (data.riksdagen.se)
aidag build-cases    # cases + actual party positions
aidag fetch-corpus   # 2022 manifestos, partiprogram, Tidöavtalet (snd.se, liberalerna.se)
aidag build-kb       # monthly point-in-time country snapshots  (Riksbanken, SCB, Wikipedia)
aidag select-pilot   # stratified pilot sample
aidag simulate       # AI party agents via Anthropic Batch API (--dry-run is free)
aidag collect        # gather batch results
aidag probe          # memorization probe (contamination measurement)
aidag aggregate      # agreement stats, confusion matrices
aidag export-site    # JSON for the Astro site
aidag verify <stage> # integrity checks (CI gate)
```

Setup: `uv sync`, then `uv run aidag --help`. Site: `cd site && npm install && npm run dev`.

## Data sources & attribution

- Voteringar och dokument: **Källa: Sveriges riksdag** ([data.riksdagen.se](https://data.riksdagen.se))
- Valmanifest och partiprogram: [SND Vivill](https://snd.se/sv/vivill) (Public Domain Mark)
- Makrodata: Sveriges riksbank, SCB. Händelser: Wikipedia (CC BY-SA 4.0)

See `docs/data-sources.md` for every endpoint and license. Code is MIT-licensed.
