# Grouped-agent execution: cost, speed, and correctness study

Measured 2026-07-24 on the `p6group` probe (party M, 40 cases) and the `p6val`
validation (party V, 48→60 cases), Claude Opus 4.8 unless noted; §6a/§7d add
Claude Opus 5 at three effort levels. Every number below was read from actual run
transcripts, not estimated — the meter (`scripts/agent_meter.py`) parses
per-message `usage` and timestamps out of the Claude Code subagent JSONL logs
after the fact.

The question: how to produce 20,312 policy-first decisions (2,539 votes × 8
parties) affordably, quickly, and correctly. Answer up front, then the evidence.

---

## TL;DR — the decided configuration

| lever | decision | why |
|---|---|---|
| execution shape | **one grouped agent per context**, self-batching | corpus read once; 7–14× cheaper than per-case |
| group size | **~40–60 cases/agent** | cost falls monotonically with size, flat above ~40 |
| write chunk | **10 cases/block** | 5 adds a cache blowout; 20 is silently ignored |
| model | **Opus 5** | 25% cheaper and 38% faster than Opus 4.8 at equal grounding (§6a). Sonnet costs *more* (token thrash); Haiku fabricates + decides differently |
| effort | **`high`** | `medium` costs the same and drifts; `xhigh` costs 48% more for *identical* decisions (§6a) |
| citation prompt | **`many` (3–4, decisive-first)** | maximizes distinct grounded citations; `few` is dominated |
| citation ordering | **do not present `citations[0]` as "the" reason** | targeting is noise-limited (see §5) |
| metric | **plan-vs-behaviour gap, not vote accuracy** | the product is the divergence, not a prediction |

Result at the operating point (Opus 5 / `high`, n=60, measured on party V):
**$0.105 / decision, 100% verbatim citations, 3.95 citations/decision,
94–96% cross-model stance agreement.** Full run ≈ **$2,200** and ~18 h wall
(concurrency-bound), across 358 agents. Execute via
`docs/orchestration-full-v4.md`.

*(The superseded Opus 4.8 operating point was $0.128/decision, ≈$2,600 full run.)*

---

## 1. Method and pricing

**Meter.** `agent_meter.py` reads every `agent-*.jsonl` transcript under
`~/.claude/projects/<proj>/*/subagents/…`, keyed by (run, agent id) with dedupe
for resumed sessions. Two correctness details:
- streaming writes several partial `usage` records per message → keep the one
  with the largest `output_tokens` per message id;
- an arm's **wall** time is `max(end) − min(start)` (what an operator waits, with
  in-arm parallelism folded in); **serial** time is the sum of per-agent wall
  (what scales once the concurrency cap is saturated);
- **tool calls** count `tool_use` blocks deduped per message id. This was added to
  the meter after §6 was first written, and reads 1–3 lower than the hand-counted
  figures originally recorded there (Opus 4.8 17 vs 18, Sonnet 46 vs 49, Haiku 25
  vs 27). Every arm now goes through the same counter, so comparisons hold; the
  small offset is a counting-method difference, not a model difference.

**Prices** (per MTok, standard tier):

| model | input | output |
|---|---|---|
| Opus 5 | $5.00 | $25.00 |
| Opus 4.8 | $5.00 | $25.00 |
| Sonnet 5 | $3.00 ($2.00 intro through 2026-08-31) | $15.00 ($10.00 intro) |
| Haiku 4.5 | $1.00 | $5.00 |

Cache **writes** cost 1.25× input (5-minute TTL) or 2× (1-hour); cache **reads**
cost 0.1× input. The cache-read discount is the whole game — see §2.

**"Billed tokens"** in the tables = fresh input + cache writes + cache reads +
output (every unit that costs money), not just uncached input.

---

## 2. Execution shape — the cost lever is group size

Same 40 party-M cases, five ways of executing them:

| shape | agents | $/dec | tokens/dec | cached | wall | serial |
|---|---|---|---|---|---|---|
| **grouped-40** (1 agent, self-batched) | 1 | **0.094** | 43.7k | 82% | 14m49s | 14m49s |
| fan-out 8×5 (8 agents) | 8 | ~0.207¹ | ~75k | 78% | ~3m | ~25m |
| piped 8×5 (1 live agent, 8 asks) | 1 | 0.359² | 131k | 70% | — | — |
| individual (1 agent/case) | 40 | 0.682 | 559k | 91% | 9m58s | 69m30s |
| sequential (1 agent, turn/case) | 1 | 1.094² | 1,490k | 98% | 39m40s | 39m40s |

¹ projected from a killed run (8 agents, corpus reads done at 49s = $5.13) plus the fitted marginal.
² measured on the partial before it was stopped (piped: 10/40; sequential: 17/40).

**Why group size dominates.** The party corpus under p6 is ~36k tokens
(`valmanifest` + `partiprogram`); a single case's user message is ~0.7k. That's a
**52:1 fixed-to-marginal ratio.** A grouped agent reads the corpus once and
amortizes it over N cases; a per-case agent re-reads it every time. Two-point fit
(n=1 and n=40):

> **cost(n) ≈ $0.604/agent fixed + $0.0784/case**   (curve flat above n≈10)

**Keeping an agent alive costs *more*, not less — the counter-intuitive result.**
Both the piped (8 asks into one agent) and sequential (1 turn per case) shapes
re-pay the context on *every ask*. Each new message into a live agent invalidates
the prompt-cache prefix and rewrites the whole accumulated context at the 1.25×
**write** rate instead of the 0.1× **read** rate — a **12.5× penalty per batch
boundary**, on a context that grows ~20k per batch. `grouped-40` takes that hit
once; the piped shape takes it eight times. This is why re-reading a 36k corpus in
8 fresh agents (fan-out, $0.207) beats one agent that reads it once but is fed 8
asks (piped, $0.359).

Concretely, the piped agent's per-turn usage showed cache-writes of 103k and 125k
at the two batch boundaries against near-zero reads — the full context rewritten,
twice.

---

## 3. Group-size sweep (citation prompt, Opus)

| group | $/dec | citations/dec | tokens/dec | cached | wall | cache blowouts |
|---|---|---|---|---|---|---|
| 20 | 0.202 | 3.90 | 94.7k | 83% | 15m12s | 1 @136k |
| 30 (chunk 5) | 0.137 | 3.90 | 77.3k | 81% | 15m28s | 2 @141k,165k |
| **60** | **0.128** | **3.95** | 66.7k | 88% | 36m49s | 1 @111k |
| 40 (old prompt) | 0.094 | 2.47 | 43.7k | 82% | 14m49s | 1 @137k |

Cost falls monotonically with group size and flattens by 60 — going 40→60 saves
only fractions of a cent per decision. Citation density is **scale-free**: 3.90 /
3.90 / 3.95 across the range, and within the n=60 run the newly-added cases scored
*higher* than the originals (4.00 vs 3.92). Group size buys cost; the prompt buys
citations; they don't interact.

---

## 4. Write-chunk size — a thing to not get wrong, not a lever

A/B on identical 20 cases and prompt:

| chunk | $/dec | tokens/dec | turns | largest single output | blowouts |
|---|---|---|---|---|---|
| **10** | **0.202** | 94.7k | 17 | 33.8k | 1 |
| 20 | 0.228 | 137.5k | 22 | 29.7k | 1 |
| 5 (from n=30 run) | — | — | 19 | — | 2 |

Chunk 20 cost **13% more for identical quality** — and the model **ignored the
instruction**: told to write one block of 20, its largest response was *smaller*
(29.7k) than chunk-10's (33.8k), spread over *more* turns (22 vs 17). There is a
natural ~30k-output-token ceiling per response; asking for a bigger block just
adds full-context round trips. Chunk 5 (tried at n=30 to dodge the blowout) added
a *second* blowout instead. **Chunk 10 is correct.**

---

## 5. Citation regime — prompting controls count, not targeting

Same 20 cases, gold standard = a fresh per-case ("one-by-one") Opus read.

| prompt | citations/dec | $/dec | verbatim | decisive-citation = gold | stance = gold |
|---|---|---|---|---|---|
| **many** (3–4) | 3.90 | 0.202 | 100% | 60% | 90% |
| few (1–2, "report only the strongest") | 1.30 | **0.201** | 100% | 45% | 90% |
| old ("at least one") | 2.47 | 0.094 | 100% | 55% | 85% |

**Two findings:**

1. **"Fewer but targeted" is strictly dominated.** The `few` prompt cost the
   *same* as `many` ($0.201 vs $0.202) — the expense is the corpus *search*, not
   the citing — while delivering a third of the evidence and *no better*
   targeting. It obeyed the count (14 cases got 1 citation, 6 got 2) but ignored
   "prefer the narrowest passage" (median quote came out *longer*, 93c vs 81c).

2. **Citation targeting is noise-limited.** Agreement with the gold arm's
   *decisive* (first) citation was **45–60% under every prompt tried**, and two
   runs of the *same* prompt agreed on the decisive citation only **60%** of the
   time. There is no stable "the decisive quote" — the model samples from a pool
   of roughly-equally-valid groundings and orders them near-arbitrarily.
   **Product consequence:** do not present `citations[0]` as "the commitment that
   carries the vote"; show citations as an unordered evidence set.

What the extra citations *do* buy is real breadth, not padding: **100% of
citations beyond the 2nd name a distinct `princip`**, spanning a median 37% of the
corpus. But they barely move the decision — stance changed on only 2/40 cases
between the thin and rich prompts.

---

## 6. Model tier — Opus wins, and Sonnet is the surprise

Same 20 cases, same `many` prompt, chunk 10:

| model | $/dec | tokens/dec | tool calls | wall | citations/dec | verbatim | stance = gold |
|---|---|---|---|---|---|---|---|
| **Opus 4.8** | 0.202 | 94.7k | 18 | 15m12s | 3.90 | **100%** | **90%** |
| Sonnet 5 | 0.235 | 393.4k | 49 | 17m37s | 3.00 | 100% | 90% |
| Haiku 4.5 | 0.019 | 84.0k | 27 | 5m06s | 3.30 | **95%** | **65%** |

**Sonnet costs *more* than Opus** ($0.235 vs $0.202) despite a lower per-token
price, because it burned **4.2× the tokens over 49 tool calls vs 18** — it thrashed.
*Model list price is not task cost.* (At Sonnet's intro pricing through
2026-08-31 it's ~$0.157/dec, genuinely cheaper — but the 4.2× token consumption is
structural and the discount expires.)

**Haiku is disqualified on two counts.** Its ~11× cost saving is real, but (a) it
fabricated 3/66 quotes — and the failure mode is the dangerous one: it copies a
genuine passage for ~60 characters, then *invents the tail* in fluent
manifesto-prose that no prefix check catches (only exact-substring verification
does); and (b) it **decided differently on 35% of cases** — stance agreement with
the gold arm was 65% vs Opus/Sonnet's 90%. A cost saving is worthless if a third
of the votes change. Also structural: Haiku's 200K context vs Opus/Sonnet's 1M is
near the n=20 peak (~167k), so it would likely fail outright at n≥40.

---

## 6a. Opus 5, and the effort dial

Same 20 party-M cases, same `many` prompt, chunk 10 — only the model and
`output_config.effort` differ. Opus 5 is priced identically to Opus 4.8
($5/$25 per MTok), so every cost difference below is **fewer tokens and fewer
turns, not a cheaper rate**.

| arm | $/dec | tokens/dec | turns | tools | wall | cached | citations/dec | verbatim | stance = gold |
|---|---|---|---|---|---|---|---|---|---|
| Opus 4.8 | 0.202 | 94.7k | 17 | 17 | 15m12s | 83% | 3.90 | 100% | 90% |
| **Opus 5 `high`** | **0.152** | **78.7k** | 16 | 15 | **9m27s** | 83% | 3.95 | **100%** | 85% |
| Opus 5 `medium` | 0.152 | 78.4k | 16 | 15 | 8m57s¹ | 82% | 3.85 | 100% | 85% |
| Opus 5 `xhigh` | 0.225 | 91.7k | 17 | 17 | 13m29s¹ | 76% | 3.85 | 100% | 85% |

¹ measured under 2-way concurrency; token and dollar figures are unaffected.

**Opus 5 at `high` beats the 4.8 operating point on every axis at once** — 25%
cheaper, 38% faster, 17% fewer tokens — with citation density and grounding
unchanged.

**The effort dial is flat below `high`, then jumps.** `medium` and `high` are the
same run for cost purposes (78.4k vs 78.7k tokens, $3.04 vs $3.04, same 16 turns
and 15 tool calls); `xhigh` costs 48% more. The reason is structural: this
workload is ~82% cache reads and structured writing, not reasoning, so there is
almost no thinking headroom to cut. At `xhigh` the model finally does think
substantially more (cached% 83→76, tokens +17%) — and **changed zero decisions**.

Both directions from `high` are therefore worse or neutral:

| | vs `high` |
|---|---|
| `medium` | same cost, but drifts on 2/20 stances (90% agreement vs `high`↔`xhigh`'s 100%), and matches the gold decisive citation least often (50% vs 60%/65%) |
| `xhigh` | +48% cost, 20/20 identical decisions, and the only arm *more expensive than Opus 4.8* |

**The 90%→85% stance-vs-gold move is one case, not a regression.** All three
Opus 5 arms agree with Opus 4.8 on 19/20, and `high`↔`xhigh` agree 20/20 — the
decisions are model-determined here, not effort-determined. Opus 5 also called
fewer cases `not_covered` (2 vs 4) and picked narrower quotes (median 67c vs 81c).

---

## 7. Correctness — the metric was wrong, then the config validated

All of §2–6 was measured on **party M, which votes Ja 2,530/2,539** — so it could
measure citation fidelity and self-consistency but **never vote accuracy** (on an
all-Ja set, "always predict Ja" scores 100%). The first mixed-outcome test:

**Party V, 2024 programme, balanced 20 Ja / 20 Nej / 8 Avstår (40 scorable).**

### 7a. As a vote predictor — looks like failure

| metric | value |
|---|---|
| derived-vote accuracy | 23/40 (57.5%) |
| majority-class baseline | 20/40 (50%) |
| significance vs chance | **p = 0.21 — not significant** |
| confusion | predicts Nej 35/40; real Ja 4/20 right, real Nej 19/20 right |

Not a scoring inversion (flipping the map gives 17/40, worse). The model applies a
strong one-directional prior. For context, the data **currently live** on
airiksdagen.se (`full-v3`, p5, Sonnet) scores 88% overall — but that's inflated by
the all-Ja governing parties; on the 5 genuinely mixed parties it's ~78% weighted,
and on V specifically **65%**. V is the hardest party for both configs.

### 7b. As a plan-vs-behaviour gap — validates

The policy-first design **deliberately decouples** `hallning` (what the party's
plan implies) from `rost` (how they actually voted). The product is the **gap**:

| | value |
|---|---|
| plan speaks on | 40/40 cases |
| aligned (plan-implied = actual) | 23/40 (58%) |
| **gap (voted against own plan)** | **17/40 (42%)** |
| gap direction | **16 of 17: plan→oppose, voted Ja** |

Coherent story: V's left programme implies opposition to the governing committee
line, yet V voted with it 16 times — the reportable divergence. The 57% that read
as failure *is* the 42% gap.

### 7c. Is the gap real, or a lazy prior? — reproducibility settles it

Two **independent** grouped Opus reads of the same 48 V cases:

| test | result |
|---|---|
| **stance reproducibility** (identical `hallning`) | **47/48 (98%)** — higher than M's 88% |
| gap reproduces (same cases flagged) | 16/17 (94% Jaccard) |
| gap rate stability | 42% vs 40% |
| citations | 100% verbatim both runs |

A 98% agreement across independent reads means the plan-implied stance is a
**robust property of V's documents**, not sampling noise — and the model
discriminates (picks `avvisar` on a specific handful, the same handful both
times). The internal-validity signals from a single run were weaker and
inconclusive at n=40 (coverage-vs-gap flat at 44% vs 42%; confidence
weak-monotone 33/42/57%), which is *why* the reproducibility cross-check was
necessary.

**Verdict:** stable, grounded (100% verbatim), reproducible plan-vs-behaviour gap.
Valid for a product framed as *"what an AI concluded from the plan alone, vs the
real vote."*

**Boundary of the claim:** reproducibility proves the read is *stable*, not the
*only* correct one — both passes share model, prompt, and the narrowed 2-doc p6
corpus, so they could share a blind spot. The likely cause of the large gap vs the
live p5 data is that corpus narrowing (p6 = 2 docs; p5 = 4, incl. Tidöavtalet +
budget), which is a methodology choice, not an execution defect. Gap **rate** will
vary hugely by party — governing parties (plan→Ja, vote Ja) ≈ 0%; opposition high.

### 7d. A different model breaks the shared-bias tie

§7c proved the read was *stable*; its stated boundary was that both passes shared
model, prompt and corpus, so they could share a blind spot — and that no automated
test breaks that tie. **A different model does.**

The V 2024 set was extended 48→60 (`scripts/p6val_extend.py`, keeping the original
48 cids as a prefix and holding the outcome proportions at 25 Ja / 25 Nej / 10
Avstår) and re-run on Opus 5 / `high`.

| comparison | stance agreement on the shared 48 |
|---|---|
| Opus 4.8 pass1 vs pass2 | 47/48 (98%) — within-model reproducibility |
| **Opus 5 vs 4.8 pass1** | **46/48 (96%)** |
| **Opus 5 vs 4.8 pass2** | **45/48 (94%)** |

Cross-model agreement sits barely below within-model reproducibility, and the
stance mixes are near-identical (41/7, 40/8, 41/7 stödjer/avvisar). The
plan-implied stance is a property of V's documents, not an artifact of Opus 4.8.

The gap reproduces at **44%** (22/50), 21 of 22 in the plan→Nej-but-voted-Ja
direction, with 237/237 citations verbatim. Derived-vote accuracy is 28/50 (56%)
— and on the original 40 scorable cases Opus 5 scores 23/40, *identical* to 4.8
pass1.

**The internal-validity signals §7c called inconclusive are now clean:**

| signal | §7c @ n=40 (Opus 4.8) | @ n=60 (Opus 5) |
|---|---|---|
| gap by coverage | flat — 44% vs 42% | **explicit 29% / inferred 52%** |
| gap by confidence | weak — 33/42/57% | **high 29% / med 48% / low 67%** |
| accuracy by confidence | — | **low 33% / med 52% / high 71%** |

Both are now monotone in the right direction: the model's own uncertainty
predicts where it diverges from reality. That is what distinguishes reading the
case from applying a flat prior, and it is the strongest internal evidence the
study has produced.

---

## 8. Full-run projections (20,312 decisions)

| configuration | cost | serial time | note |
|---|---|---|---|
| **Opus 5 `high`, `many`, n=60** | **~$2,200** | ~180 h | the decided config; $0.105/dec measured |
| Opus 4.8, `many`, n≈60 | ~$2,600 | ~110–210 h | superseded; ~$0.128/dec |
| Opus, old thin prompt, n=40 | ~$1,910 | ~125 h | 2.47 vs 3.9 citations |
| Sonnet, `many`, n=20 rate | ~$4,800 (~$3,200 intro) | — | dominated (token thrash) |
| Haiku, `many`, n=20 rate | ~$390 | — | disqualified (fabrication + 35% stance drift) |

Wall time = serial ÷ concurrency (~10 agents in parallel) ⇒ roughly **18 h**.

Splitting must respect context boundaries: a grouped agent reads one party's
corpus, so the 20,312 decisions divide within **34 (party, p6-context) buckets**
(enumerated from `corpus.context_key`; the earlier "15" was an estimate), not
into arbitrary equal groups. At n≤60 that is **358 agents**. Bucket sizes run
2..2,539; the five sub-20 buckets are only 40 decisions (0.2% of the run), so
their poor amortization ($0.16–0.78/dec) does not move the total.

Two-point fit from the Opus 5 arms (n=20 party M, n=60 party V):

> **cost(n) ≈ $1.40/agent fixed + $0.082/case**

Applied to the real bucket structure this gives **~$2,170**, against ~$2,133 for
the naive `20,312 × $0.105` — i.e. bucket fragmentation costs under 2%.

### Measured in production (full-v4 batch 1, 2026-07-25)

The first real batch — 60 agents, 3,000 decisions, all 8 parties, Opus 5 `high`:

| | $/dec | tokens/dec | median wall | agents |
|---|---|---|---|---|
| full groups (n=60) | 0.116 | 88.2k | 24m | 48 |
| remainder groups (<60) | **0.240** | 120.3k | 8m | 12 |
| **whole batch** | **0.121** | 89.5k | 23m | 60 |

Batch wall 2h58m (serial 22h, so ~7.5× effective concurrency). **11,454/11,454
citations verbatim (100%)**, 3.82 citations/decision, and only the two p6-legal
documents cited — no p5 corpus leakage.

$0.121/dec is **15% above the $0.105 single-agent V60 point**, which projects the
full run to **~$2,465** (still under Opus 4.8's ~$2,600). Two contributors, in
order of size: production agents took ~27 turns against V60's 23, and each turn
re-reads a growing written-context, so tokens/dec rose 51k → 88k; and remainder
groups cost 2× per decision. Corpus size is *not* the cause — production corpora
run 32–60k tokens against V2024's 42k.

Remainders are only 4% of decisions here (~$15/batch, ~$90 over the run), so
they are worth reducing with a larger `--batch-size` but are not the main gap.

**Operational trap:** `agent-prepare --batch-size` caps the batch *before*
grouping, so a small batch leaves only a handful of cases per bucket and
`--group 60` silently yields groups of 7–15 — roughly double the per-decision
cost. Measured: `--batch-size 120` produced sizes 7–15; `--batch-size 3000`
produced a median of 60 (48 of 60 groups full) at $0.110/dec. Always check the
size distribution after preparing.

---

## 9. Open questions and caveats

- **Accuracy validated on one context (V, the hardest).** The gap frame passed at
  98% reproducibility there. A governing party (near-0% expected gap) has not been
  run — it would confirm the other end of the range.
- ~~**Reproducibility ≠ ground truth.** Two runs sharing model/prompt/corpus can
  share a systematic bias. No automated test breaks that tie.~~ **Partly
  addressed (§7d):** an independent model (Opus 5) agrees with the two Opus 4.8
  passes 94–96%, against 98% within-model. Prompt and corpus are still shared,
  so a *prompt*- or *corpus*-induced bias remains untested.
- **The p6 2-doc corpus** costs measurable divergence from the live 4-doc p5 data.
  Whether that's the intended party-blind narrowing or an over-narrowing is a
  methodology decision, not an execution one.
- **Party M is useless for accuracy** — all-Ja. Always validate config changes on
  a mixed-outcome context (C 2013, V 2024, MP 2013).

---

## 10. Tooling produced (all reusable, parameterized)

| script | purpose |
|---|---|
| `scripts/agent_meter.py` | cost + wall/serial time + turns/tool-calls per agent, read from transcripts after the fact; `--list` enumerates all runs |
| `scripts/p6group_arms.py` | citation density / verbatim / stance-vs-gold for **any** set of arm dirs — the parameterized scorer used for the model and effort sweeps |
| `scripts/p6val_extend.py` | grow a p6val set to N, keeping existing cids and the Ja/Nej/Avstår proportions |
| `scripts/p6group_cited_workflow.js` | the parameterized grouped run — `{n, chunk, group, out, cites: many\|few, model, dir, sys}` |
| `scripts/p6group_report.py` | citation density / verbatim / stance vs baseline on party-M arms |
| `scripts/p6group_extend.py` | grow a case set to N within one context (keeps existing cids) |
| `scripts/p6group_split.py` | split a case set into fixed-size groups |
| `scripts/p6val_prepare.py` | build a stratified mixed-outcome accuracy sample for any party/context |
| `scripts/p6val_report.py` | score derived-vote accuracy vs reality (confusion, calibration, citations) |
| `scripts/p6val_gap_report.py` | score as a plan-vs-behaviour gap + internal-validity check |

Validation data lives under `data/interim/p6val/V2024/` (`out/`, `out2/` = the two
reproducibility passes).
