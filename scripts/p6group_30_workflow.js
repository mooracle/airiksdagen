// Group-of-30 arm with a citation-targeted prompt.
//
// Two changes from the grouped-40 arm, both aimed at its one measured defect
// (2.30-2.60 citations/decision vs 3.80-4.03 one-by-one; quotes never wrong,
// just too few):
//
//   1. PROMPT. grouped-40 said "Always give at least one citation" — a floor,
//      which the model satisfied minimally when spreading attention over a
//      10-case chunk. This asks for 3-4, ordered decisive-first, and says when
//      one is actually acceptable. The decisive-first / princip wording is
//      restored from the p5 prompt, which did not have the density problem.
//   2. CHUNK. Writes in blocks of 5 rather than 10. grouped-40's first write
//      emitted 29k output tokens and invalidated a 137k cached prefix (turn 10:
//      cache_creation 136,941 / cache_read 0). Smaller blocks should keep more
//      of the run on 0.1x cache reads.
//
// Group size 30, cids 000-029 — a subset of the same 40 cases the other arms
// decided, so it scores against them on identical inputs.
//
//   Workflow({ scriptPath: "scripts/p6group_30_workflow.js" })
//   uv run python scripts/agent_meter.py "group30.json"=grouped-30=30

export const meta = {
  name: 'p6group-30-cited',
  description: 'Decide 30 p6group cases in one agent, citation-targeted prompt',
  phases: [{ title: 'Decide', detail: '1 agent, 30 cases, blocks of 5', model: 'opus' }],
}

const G = '/Users/mgyk/mooracle/aidag/data/interim/p6group'
const N = 30
const CHUNK = 5

const RECEIPT = {
  type: 'object',
  properties: {
    written: { type: 'number' },
    mean_citations: { type: 'number' },
  },
  required: ['written', 'mean_citations'],
  additionalProperties: false,
}

const prompt =
  `You decide how a Swedish party SHOULD vote in ${N} SEPARATE Riksdag divisions, and write each decision to a file.\n\n` +
  `1. Read ${G}/system/M.txt — your role, rules and the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read ${G}/group30.json — its "cases" array holds all ${N} cases, each with a "cid" and a "user" field.\n` +
  `3. Read ${G}/schema.json — every decision must conform to it.\n\n` +
  `Deciding — rules:\n` +
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. Do NOT seek consistency across cases and do NOT let one decision influence another; the same commitment can point one way in one division and the other way in another.\n` +
  `- Base each decision ONLY on the documents in the role file and that case's own text.\n` +
  `- Do NOT use knowledge of how the party actually voted, news, or later events.\n` +
  `- Do NOT weigh in party tactics, coalition loyalty or how other parties vote.\n` +
  `- Uncertainty is valid: use confidence="low" / coverage="not_covered" when the plan does not reach the case.\n\n` +
  `CITATIONS — this is the part that matters most, and the part most often done thinly:\n` +
  `- Give 3 to 4 citations for a typical decision. Drop to 1-2 ONLY when the plan genuinely touches this case in just one or two places; if you find yourself writing a single citation repeatedly, you are under-reading the documents.\n` +
  `- Order them by importance: the FIRST citation must be the commitment that actually carries the vote, not a generic statement of values. Later citations add supporting or qualifying commitments.\n` +
  `- Each "quote" MUST be a verbatim substring of the documents in the role file — copy it exactly, do not paraphrase, do not repair grammar, do not join text across a gap. Prefer the specific passage over the general one.\n` +
  `- Give each citation a short "princip" (2-6 Swedish words) naming the commitment it encodes, e.g. "minskad asylinvandring".\n` +
  `- Do not pad: a fourth citation that repeats the third adds nothing. Distinct commitments only.\n\n` +
  `Fields: hallning ("stodjer"|"avvisar" — the plan's stance on what the MOTFORSLAG demands), confidence, coverage, motivering (Swedish, 2-4 sentences), citations [{document,quote,princip}], omvarld {paverkar,faktorer}, flags, plan_tacker_utskottets_skal ("ja"|"nej", answered independently of hallning).\n\n` +
  `Writing: append decisions as JSON Lines to ${G}/out_g30/decisions.jsonl, in BLOCKS OF ${CHUNK} cases (write cases 0-4, then 5-9, and so on). Write each block before deciding the next one — do not hold decisions back to the end. Each line is one JSON object conforming to the schema PLUS a "cid" field copied exactly from the group file. No markdown fences, no commentary in the file.\n` +
  `Cover all ${N} cases. Reply via the structured output with the number written and your mean citations per decision.`

phase('Decide')
log(`grouped-30 (citation-targeted prompt): 1 agent, ${N} cases, blocks of ${CHUNK}`)

const r = await agent(prompt, {
  label: `grouped-30`,
  phase: 'Decide',
  model: 'opus',
  agentType: 'general-purpose', // needs Read + Write
  schema: RECEIPT,
})

log(r ? `done: ${r.written} decisions, agent reports ${r.mean_citations} citations/decision`
      : 'agent errored — nothing written')
return r ?? { written: 0, mean_citations: 0 }
