# Metod (sammanfattning)

Den fullständiga metodbeskrivningen renderas på webbplatsen (`/metod/`,
`site/src/pageviews/Method.astro`). Kärnan:

- **Rekonstruktion, inte prediktion.** Modellens träningsdata innehåller
  sannolikt de verkliga utfallen; vi mäter dokumenttrohet, inte prognosförmåga.
- **Enhet:** huvudvotering i sakfrågan, 2022–2026 (Riksdagens öppna data).
- **Agent:** en förfrågan per votering × parti; underlag = valmanifest 2022
  (SND Vivill), Tidöavtalet för M/KD/L/SD fr.o.m. 2022-10-14, samt månadsvis
  lägesbild med publiceringsvintage (ingen information efter beslutsmånaden).
  Opinionsmätningar ingår medvetet **inte** i agentens underlag — partiet ska
  följa sin plan, inte opinionen; väljarstödet visas bara på webbplatsen.
- **Omvärldsläge (p4):** agenten får ett datumspecifikt omvärldsblock
  (styrränta, inflation, BNP-indikator, barometer, växelkurs, elpris,
  asylansökningar — alla med publiceringsvintage — samt ~10 händelser/frågor
  från de senaste 30 dagarna: riksdagens egna interpellationer/frågor,
  krisinformation, Wikipedia). Dokumenten förblir grunden; omvärlden får
  vägas in endast när den väsentligt påverkar tillämpningen och redovisas då
  strukturerat (`omvarld.paverkar` + faktorer). Källor och regler:
  `docs/worldstate-plan.md`.
- **Citatkontroll:** citat verifieras maskinellt som ordagranna utdrag;
  parafraserade citat (~2 % hos Sonnet) rättas deterministiskt till närmaste
  faktiska textställe och flaggas `citat_korrigerat` öppet på webbplatsen.
- **Läckagekontroller:** inga ärendenummer/datum i prompten, anonymiserade
  motförslag, aldrig riksdagens beslutsnotis (endast utskottets
  förhandssammanfattning), maskering av dokumentreferenser. Regex-verifierat i
  `tests/test_promptgen.py`.
- **Kontaminering mäts** med minnesprober (`aidag probe`) och redovisas per
  parti och ärende.
- **Verifiering:** vår röstaggregering korsvalideras mot riksdagens egna
  partitabeller (0 avvikelser över samtliga voteringar); AI-citat verifieras
  som exakta utdrag ur källdokumenten.

Se `docs/data-sources.md` för alla källor och licenser.
