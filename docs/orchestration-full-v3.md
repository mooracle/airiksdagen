# full-v3 — operator runbook

The complete simulation: 2,539 voteringar × 8 parties (20,312 decisions) + 2,539
memorization probes, every decision produced the same way. Supersedes
`orchestration-grouped-run.md` (full-v2) and `orchestration-full-run.md`.

## Why full-v3 replaces full-v2

full-v2 is not wrong-by-a-little; it is **under-specified for half the parties**.
Its agents saw only a party's 2022 election manifesto, plus Tidöavtalet for the
government side. Those manifestos are tiny — MP 3,681 words, S 4,556 — while the
government parties carried 19,539 extra words of Tidöavtalet. The result:

| | own words (p4) | not_covered |
|---|---|---|
| MP | 3,681 | 29% |
| S | 4,556 | 27% |
| KD | 1,666 | 19% (but 100% agreement anyway — coalition carries it) |
| SD | 21,160 | 7% |

Opposition parties were reasoning from almost nothing, and a government party
with nothing to go on still votes Ja and scores 100%. So full-v2's headline
numbers (78.3% agreement, MP at 55%) measure starved inputs, not party behaviour.
full-v2 is **kept as a record but never extended or published as the headline**.

## Run identity — fixed for the whole run

| | |
|---|---|
| run_id | `full-v3` |
| prompt | **p5** |
| arm | `anonymous` |
| execution | grouped, size-driven (`--group 0`) |
| model | one model for the whole run (mixing makes per-party stats incomparable) |
| batch_id at ingest | `claude-code-workflow-p5` |
| workflow script | `scripts/grouped_batch_workflow.js` |

## p5: what each party sees

Every party gets **its own documents and no one else's**, and each document is
visible **only from its own adoption/submission date onward** — the rule
Tidöavtalet already followed, generalised.

| document | who | from |
|---|---|---|
| valmanifest 2022 | all 8 | always (authoritative where documents conflict) |
| partiprogram | all 8 | its adoption date; the standing version rolls over mid-term |
| Tidöavtalet | M/KD/L (signatories), SD (support) | 2022-10-14 |
| budgetmotion | S/V/C/MP only | its submission date, **except on the division that votes on it** |

Only S, V, C and MP file a shadow budget: M/KD/L present the actual
budgetproposition and SD backs it. So the four parties with a shadow budget are
exactly the four with the thinnest manifestos.

### Why date-gating, not one snapshot

Neither extreme works:

- **All-old** is blind. S's 2013 programme mentions Nato zero times, through a
  term dominated by the accession.
- **All-current** is contaminated. MP's 2025 programme contains
  *"När Sverige anslöt sig till Nato valde Miljöpartiet att rösta emot
  beslutet"* — the party stating **how it voted on a division in this dataset**.
  Shown to that vote, an agent reads the answer off the page.

S, V and MP fully rewrote their programmes mid-term (1–4% textual similarity to
the old ones); SD, KD and L only amended theirs (95–98%). Five parties' live
websites now serve post-election text, so the URLs in `config.PARTY_PROGRAMS` are
pinned and several point at SND rather than the party's own domain.

Result: **34 distinct context states** across 8 parties × 4 years — enumerable
and publishable, not churn. `M` never changes; the opposition parties change most
because their shadow budget rolls over each autumn.

### Leak controls

- Reservation **authorship is scrubbed** (a party votes Nej on its own
  reservation in 3,877 of 3,878 cases — it is close to an answer key).
- A party is **never shown its own shadow budget on the division that votes on
  it** (33 case × party pairs). Keyed on dok_id from the RAW dokumentstatus
  references — `cases.parquet` truncates its reference list at 20 and 1,443 of
  2,539 cases sit at that ceiling.
- `verify simulate` fails any citation to a document that decision's agent was
  not served. The gate calls the same `corpus.documents_for` the prompt builder
  does, so it cannot drift.

## Cost: group size is the lever

The corpus is read once per agent and runs 53k–126k tokens, so **group size sets
the price**:

| group size | tokens/decision | full run |
|---|---|---|
| 8 | 19.3k | 413M |
| 24 | 11.3k | 249M |
| month-scale (size-driven) | ~11k | ~225M |

`--group 0` packs each party-month into the **fewest agents that fit**, bounded by
both limits:
- **context** — corpus + cases must fit `--context-limit` (use 1000000 on Opus)
- **output** — every decision returns in ONE response, so groups cap at 60 cases
  (~24k output). A 1M window does not buy a 1M response, and a truncated response
  loses the whole group.

Corpus compression is **not** a lever: cleanup removes 0.9%. These are dense
policy documents. Wall-clock is ~42h of agent time regardless of batching (16
agents run concurrently; per-case reasoning dominates).

## The batch loop

```text
1. PREPARE   uv run aidag agent-prepare --run-id full-v3 --prompt-version p5 \
                 --group 0 --context-limit 1000000 --batch-size 1600 --no-probes
             (probes are backfilled only once all decisions are collected)

2. RUN       Workflow { scriptPath: "scripts/grouped_batch_workflow.js",
                        args: { manifestPath: "<abs path>", model: "<model>" } }
             ⚠ args must be a JSON OBJECT, not a string.
             ⚠ Confirm the first log line names the right prompt version and
               model: "batch loaded: NN groups (... decisions), 0 probes
               (run full-v3, prompt p5, model X, citable: ...)"

3. INGEST    uv run aidag agent-ingest --run-id full-v3 --input <file> \
                 --model <model-id> --batch-id claude-code-workflow-p5
             uv run aidag repair-citations --run-id full-v3

4. VERIFY    uv run aidag verify simulate --run-id full-v3   # must be green

5. CHECKPOINT
             git add data/results && git commit -m "full-v3 batch NNN: <n> decisions"
             uv run aidag agent-status --run-id full-v3

6. TRANSLATE when pending decision-translations exceed ~1200 (see below)
```

Batches walk chronologically, so a partial run is always a coherent "first N
months" dataset.

## Changing the corpus mid-run

Don't. The system prompts are written once per context into
`data/interim/agentrun/full-v3/system/` and reused. If a corpus file changes,
**delete that directory** so the prompts regenerate — otherwise early and late
batches of the same run disagree about what the agents saw, and citation
verification breaks.

## Failure modes

| symptom | meaning | action |
|---|---|---|
| `args.manifestPath missing` | args passed as string | relaunch with a JSON object |
| `unknown prompt_version` | loader dropped the field | relaunch (costs one loader agent) |
| `manifest failed integrity check` | loader transcription corrupted | relaunch |
| sims count < manifest count | agents died / window exhausted | ingest what arrived, commit, stop |
| unverifiable citations after repair | corpus and prompts disagree | STOP — likely a corpus change mid-run |

## Verification gates

Per batch: schema validity, custom_id uniqueness, verbatim quotes, citations only
to in-context documents (date-gated per party per case).

Every ~10 batches:

```sh
uv run aidag aggregate --run-id full-v3        # incl. coalition-vs-programme
uv run aidag export-site --run-id full-v3
cd site && npx astro build && cd ..
uv run pytest -q
```
