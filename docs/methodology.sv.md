# Metod (sammanfattning)

Den fullständiga metodbeskrivningen renderas på webbplatsen (`/om/`,
`site/src/pageviews/AboutPage.astro`). Kärnan:

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
- **Citatkontroll:** citat verifieras maskinellt som ordagranna utdrag ur just de
  dokument agenten faktiskt fick se. I `full-v4`: 2 rättades deterministiskt till
  närmaste faktiska textställe (`citat_korrigerat`), 120 kunde inte verifieras och
  tömdes med agentens egen formulering bevarad bredvid (`citat_ej_verifierat` /
  `quote_ej_verifierad`), och 520 skrevs om när källdokumenten extraherades på nytt
  ur sina PDF:er — enbart avstavning och blanksteg, originalet kvar i
  `quote_fore_migrering` (`citat_migrerat`). Allt flaggas öppet på webbplatsen;
  engelska översättningar av ett tömt citat hålls tillbaka vid export.
- **Omvänt register:** varje citat indexeras tillbaka till det textblock det
  citerar (`aidag build-anchors`), så varje citerad rad i dokumentvyn visar vilka
  voteringar som lutade sig mot den och om partiet sedan röstade med sin egen plan.
  Ett citat som landat på en rubrik eller etikett märks som sådant — 6 block / 84
  citat i `full-v4`; se `docs/topic-label-citations.md`.
- **Läckagekontroller:** inga ärendenummer/datum i prompten, anonymiserade
  motförslag, aldrig riksdagens beslutsnotis (endast utskottets
  förhandssammanfattning), maskering av dokumentreferenser. Regex-verifierat i
  `tests/test_promptgen.py`.
- **Kontaminering hanteras strukturellt, inte statistiskt:** agenten får aldrig
  veta vilken votering den ser, så den kan inte hämta ett memorerat utfall.
  Läckagekontrollerna ovan är försvaret, och de är maskinellt upprätthållna mot
  den skarpa promptversionen. Att memorering skulle krympa gapet — men det
  uppmätta gapet är stort och nästan helt ensidigt — pekar åt samma håll.
- **Verifiering:** vår röstaggregering korsvalideras mot riksdagens egna
  partitabeller (0 avvikelser över samtliga voteringar); AI-citat verifieras
  som exakta utdrag ur källdokumenten.

Se `docs/data-sources.md` för alla källor och licenser.
