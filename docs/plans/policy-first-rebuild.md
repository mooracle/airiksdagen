# Policy-first rebuild — design spec

Status: **draft for review** (2026-07-21). Supersedes the vote-matching framing of
`full-v3`. Target run id: **`full-v4`**.

## 1. Why

The system currently optimizes the agent toward **matching the real floor vote** and
scores everything against it. Three problems, all confirmed on a worked case
(votering `0C008E7E`, SkU15 punkt 5, *F-skatt och näringsverksamhet*):

1. **Single-`rost` collapse.** The output schema folds *policy position* and
   *parliamentary behaviour* into one `Ja/Nej/Avstår`. The agent gave the correct
   **policy** answer for S/MP/C (sympathy for the reservation) and was scored wrong
   because those parties **tactically abstained**. A single field can't hold both.
2. **Thin, one-sided input.** The only case-specific policy content in the prompt was
   one reservation sentence; the Ja side was a hollow `avslår motionerna [nr]…` list,
   and the "Sammanfattning" was the whole-betänkande blurb (12 points), not this vote.
3. **The metadata layer is unfinished and mischaracterises contested cases.**
   `meta.agent.*` (the party-blind, prompt-facing view) is **empty**, and
   `meta.at_stake` frames F-tax as *deregulation / simplification* — the opposite of
   the live reservation (tightening against false self-employment). Both because
   **Phase 2** (real Nej/committee substance → regenerate `at_stake` + agent view) and
   **Phase 3** (wire `agent.*` into the prompt) from `orchestration-metadata.md` were
   never done.

Re-target: the **policy stance** is the product; the **floor vote** is secondary
context; the **gap between them** is the insight. Real votes stay as an out-of-sample
calibration/contamination signal, not the optimisation target.

## 2. What already exists (do not rebuild)

| Layer | Key | Holds | Wired to prompt? | Site? |
|---|---|---|---|---|
| `metadata` | votering_id | `subject`, `at_stake`, `subtopics`, det fields, `agent.*` **(empty)** | no | yes |
| `reservations` (built 2026-07-21) | votering_id:alt_id | per-alt `substance{sv,en}` (Nej demand) | **yes** (`Alternativ A`) | yes |
| committee position (Ja) | — | — | — | — |

The redesign **unifies** these into one per-vote brief with two audiences, and adds the
one missing piece (committee position).

## 3. Target architecture — one brief, two audiences

Assemble a per-vote **brief** from three party-blind sources; render at two altitudes.

**Sources**
- **Committee position (Ja)** — NEW. Extract `Utskottets ställningstagande` for the
  punkt from the cached betänkande fulltext, scrub, Haiku-summarise. Association rule
  (validated on the worked case): find the punkt's motion discussion in the main body,
  take the **next** `Utskottets ställningstagande`; the reservation appendix at the end
  of the doc is useless for proximity-matching. This is the load-bearing new extraction
  and MUST be validated per-case (attaching the wrong Ja rationale is worse than none).
- **Reservation demands (Nej)** — existing `reservations` layer, per alt.
- **Decision framing + stakes** — regenerate `metadata` with BOTH substances in
  `agent_src`/`display_src` so `subject`/`at_stake` become **two-sided** (the contested
  axis), not the topic label.

**Site view** (party-aware; site adds party + real outcome around a party-blind core):
`subject` · `at_stake` (two-sided) · `decision` (what the vote decides) · `ja` · `nej[]`
· `stakes` (plain-language, who it affects). Extended, reader-first.

**Agent view** (`meta.agent.*`, party-blind, de-leaked, wired into the prompt):
`decision` + `committee` (Ja) + `alternatives[]` (Nej). Compact — no `stakes`, no prose.

Same generated artifact → site renders it fully, prompt renders the compact slice, so
they never drift.

## 4. Changes

### A. Input layer

**Association backbone (found 2026-07-21 while proving the committee layer on HB01SkU15).**
The betänkande's reservation appendix indexes every reservation as
`Reservation N. <rubrik>, punkt P (PARTI)` — an EXACT key carrying number, rubrik, punkt
and party. Use it as the backbone for both layers:
- **Reservations** — associate `res-N ↔ "Reservation N"` by NUMBER (cross-check punkt +
  party). The shipped layer's `_match_bodies` matches by party-overlap+order and
  **mis-associates when a party authors ≥2 reservations in one betänkande** — confirmed:
  votering `0A1AF9BD` (punkt 10 *Personalliggare*, res-16/C) got a *visible-taxes /
  employer-fee* summary from a different C reservation. **The deployed reservation
  summaries have this class of error and must be re-extracted by number, not shipped
  as-is.**
- **Committee** — rank candidate ställningstagande blocks by motion-id coverage (top-K),
  then LLM-adjudicate + summarise. Validated on HB01SkU15: correct block chosen for
  F-skatt over a distractor. BUT agent-reported confidence is NOT reliable (a false
  "high" appeared when fed a mis-associated upstream `nej_demand`), so the validator
  must **deterministically cross-check** the chosen block's section rubrik == votering
  rubrik, not trust confidence.

1. **Fix + re-run reservations** by reservation-number association (backbone above).
2. **Committee-substance extraction** (`committee.py`, built): parse blocks → rank top-K →
   Workflow adjudicate+summarise → de-leak gate → deterministic rubrik cross-check → verify.
3. **Metadata Phase 2 regen**: pass corrected committee(Ja)+reservation(Nej) substance so
   `subject`/`at_stake` reflect the contested axis; **populate `agent.{decision,committee,
   alternatives}`** (currently empty).

### B. Prompt (`promptgen.render_user_message`)
- Replace the betänkande-wide `Sammanfattning` (`utsknotis`) with the punkt-specific
  `agent.decision` + `agent.committee` (Ja). Keep `agent.alternatives[]` for Nej
  (supersedes the current `_reservation_substance_sv` path).
- Drop the hollow scrubbed `forslag_text` list.
- Role-prompt rework: ask **stance first** (grounded in party docs), **then** the likely
  floor vote; move the `voteringsordning` explanation under the `rost` field only.

### C. Output schema (`DECISION_SCHEMA`) — the split
```jsonc
{
  "hallning":   "stödjer" | "avvisar" | "delvis" | "ingen_tydlig", // NEW: stance on the demand (product)
  "confidence": "high" | "medium" | "low",
  "coverage":   "explicit" | "inferred" | "not_covered",           // unchanged
  "motivering": "…",                                               // now anchors the stance
  "citations":  [ { "document": <enum>, "quote": "…", "princip": "…" } ], // backs the stance
  "rost":       "Ja" | "Nej" | "Avstår",                           // kept, now DERIVED
  "rost_motiv": "…",                                               // NEW: why the vote, esp. if it diverges
  "omvarld":    { "paverkar": bool, "faktorer": [...] },           // unchanged
  "flags":      [ "…" ]                                            // drop reservation-forfattare-osaker (obsolete)
}
```
The pair `hallning=stödjer` + `rost=Avstår` + `rost_motiv=…` captures the S/MP/C
principle-vs-realpolitik gap as data instead of an error.

### D. Metrics (`aggregate`) — retire the accuracy leaderboard
- **Grounding rate**: % of stances with a valid, on-point manifesto citation.
- **Stance/vote divergence catalog**: where `hallning` and actual floor vote part ways.
- **Contamination**: handled by the prompt's identifier stripping (the probe feature was
  removed — see docs/methodology.*).
- Keep real-vote agreement as *calibration only*, not the headline.

## 5. Rebuild runbook (order matters)
1. Committee-substance layer → `verify` (per-case association spot-check on ≥20 cases).
2. Metadata Phase-2 regen (two-sided `at_stake` + populated `agent.*`) → `verify metadata`.
3. Prompt wiring + `DECISION_SCHEMA` split + role prompt; update `tests` + `verify prompts`.
4. **Stratified sample re-sim (~40–80 cases)** on Opus → eyeball stances + divergences
   before committing. Go/no-go here.
5. Full re-sim → new run **`full-v4`** (2,539 × 8 = 20,312 Opus decisions; no probes — the
   cost driver; this is why the sample gate in (4) exists).
6. `aggregate` (new metrics) → `export-site --run-id full-v4` → site build → deploy.

## 6. Decisions / risks
- **Association correctness** is the top risk. Gate the committee layer with a
  per-case validator + manual spot-check; a mis-attached Ja rationale is a silent,
  high-impact error.
- **Backwards-incompatible** by choice (full rebuild). All readers of `rost` and the
  metadata `agent.*` shape change together; `full-v3` is frozen for comparison.
- **Reader neutrality**: `stakes` / two-sided `at_stake` invite editorialising — keep to
  "for X, this could mean Y" framing, party-blind, no advocacy; de-leak gate stays.
- Open: do the extended site fields (`decision`/`ja`/`nej`/`stakes`) live in the
  metadata record or a separate `briefs` file? Lean: in the metadata record (one
  per-vote home), agent slice under `agent.*`.
```
