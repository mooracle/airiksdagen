# Methodology (summary)

The full methodology is rendered on the site (`/en/methodology/`,
`site/src/pageviews/Method.astro`). The core:

- **Reconstruction, not prediction.** The model's training data likely contains
  the real outcomes; we measure document-fidelity, not forecasting skill.
- **Unit:** main chamber vote on the substantive question, 2022–2026
  (Riksdag open data).
- **Agent:** one request per vote × party; inputs = the 2022 election manifesto
  (SND Vivill), the Tidö agreement for M/KD/L/SD from 2022-10-14, and a monthly
  country snapshot with publication vintages (no information after the
  decision month). Opinion polls are deliberately **not** part of the agent's
  inputs — the party must follow its plan, not the polls; support figures
  appear on the website only.
- **Worldstate (p4):** the agent receives a per-vote-date worldstate block
  (policy rate, inflation, GDP indicator, sentiment barometer, FX, electricity
  price, asylum applications — all with publication vintages — plus ~10
  events/questions from the preceding 30 days: the Riksdag's own
  interpellations/questions, official crisis notices, Wikipedia). The
  documents remain the basis; worldstate may be weighed in only when it
  materially affects their application, and is then reported structurally
  (`omvarld.paverkar` + factors). Sources and rules: `docs/worldstate-plan.md`.
- **Citation control:** quotes are machine-verified as verbatim excerpts;
  paraphrased quotes (~2% on Sonnet) are deterministically aligned to the
  closest actual passage and flagged `citat_korrigerat`, visible on the site.
- **Leakage controls:** no case numbers/dates in prompts, anonymized
  counter-proposals, never the Riksdag's post-decision summary (only the
  committee's pre-decision one), document references masked. Regex-asserted in
  `tests/test_promptgen.py`.
- **Contamination is handled structurally, not statistically:** the agent is
  never told which vote it is looking at, so it cannot retrieve a memorized
  outcome. The leakage controls above are the defence, and they are enforced
  mechanically against the live prompt version. Memorization would shrink the
  gap — the measured gap is instead large and almost entirely one-directional,
  which points the same way.
- **Verification:** our vote aggregation is cross-checked against the
  Riksdag's own per-party tables (0 mismatches across all voteringar); AI
  citations are verified as exact substrings of the source documents.

See `docs/data-sources.md` for all sources and licenses.
