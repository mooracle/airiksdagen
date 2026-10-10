# Methodology (summary)

The full methodology is rendered on the site (`/en/about/`,
`site/src/pageviews/AboutPage.astro`). The core:

- **Not a prediction.** The site links each vote to what the party's own
  documents say about the question and shows where the vote went the other way.
  The agent is never asked how the party voted: it takes a stance on what the
  counter-proposal demands (`hallning`), and the implied vote is derived in code.
  What is measured is how far a party's votes follow its own documents — not
  forecasting skill, and not the model's accuracy.
- **Unit:** main chamber vote on the substantive question, 2022–2026
  (Riksdag open data).
- **Agent:** one request per vote × party; inputs = that party's own 2022
  election manifesto (SND Vivill) and its party programme, each visible only from
  its adoption date, plus a monthly country snapshot with publication vintages
  (no information after the decision month). Bloc documents — the Tidö agreement
  and budget motions — are deliberately excluded from the live run (`p6`), so
  every party is measured against its own plan alone. Opinion polls are deliberately **not** part of the agent's
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
- **Citation control:** quotes are machine-verified as verbatim excerpts of the
  documents the agent was actually shown. On `full-v4`: 2 were deterministically
  aligned to the closest actual passage (`citat_korrigerat`), 120 could not be
  verified and are blanked with the agent's own wording kept beside them
  (`citat_ej_verifierat` / `quote_ej_verifierad`), and 520 were rewritten when the
  source documents were re-extracted from their PDFs — spacing and hyphenation only,
  original kept in `quote_fore_migrering` (`citat_migrerat`). All of it is flagged
  openly on the site; English translations of a withdrawn quote are withheld at export.
- **Reverse index:** every citation is indexed back to the corpus block it quotes
  (`aidag build-anchors`), so each cited line on a document page shows which votes
  leaned on it and whether the party then voted with its own plan. A citation landing
  on a heading or topic label is marked as such — 6 blocks / 84 citations on
  `full-v4`; see `docs/topic-label-citations.md`.
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
- **Measuring contamination (`aidag recall-probe-*`):** the structural defence
  assumes the agent cannot tell which vote it sees, but month, committee and case
  title together identify 2,499 of the 2,539 votes. The recall probe tests that
  assumption directly (after Golchin & Surdeanu, ICLR 2024): on a stratified sample
  of 150 votes the model gets only those three fields and must guess the committee
  report's number, which the topic cannot reveal, and the reservation's parties.
  Run through the Claude Code CLI with no tools, no MCP servers and hooks disabled,
  Claude Opus 5 named the right report for 27 of 150 votes (18.0%, chance 4.9%,
  exact binomial p ≈ 4e-9), falling from 9/34 votes in 2023 to 2/40 in 2026.
  Results: `data/results/probes/`. The probe shows the model *can* identify votes,
  not how much that moved a verdict; on the 27 recognised votes the published
  verdicts agree with the actual vote somewhat more often (`recall-probe-report`).
- **No-documents arm (`agent-prepare --arm nodocs`):** the same role and case text
  with the party's documents withheld, on the probe's sample
  (`--votes-file data/results/probes/sample.json`). What the agent concludes then
  is what the model already believes about the party; a verdict reads as fidelity
  to the documents only to the extent it differs from this baseline. Compare with
  `aidag compare-runs`; `verify simulate` does not apply to this arm, since quotes
  cannot be verbatim excerpts of documents the agent never saw. The arm is part of
  the cid and is skipped by `export-site` and `build-anchors`.
- **Verification:** our vote aggregation is cross-checked against the
  Riksdag's own per-party tables (0 mismatches across all voteringar); AI
  citations are verified as exact substrings of the source documents.

See `docs/data-sources.md` for all sources and licenses.
