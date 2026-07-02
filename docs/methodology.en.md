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
  decision month).
- **Leakage controls:** no case numbers/dates in prompts, anonymized
  counter-proposals, never the Riksdag's post-decision summary (only the
  committee's pre-decision one), document references masked. Regex-asserted in
  `tests/test_promptgen.py`.
- **Contamination is measured** with memorization probes (`aidag probe`) and
  reported per party and per case.
- **Verification:** our vote aggregation is cross-checked against the
  Riksdag's own per-party tables (0 mismatches across all voteringar); AI
  citations are verified as exact substrings of the source documents.

See `docs/data-sources.md` for all sources and licenses.
