// Piped micro-batch arm: ONE agent, 8 batches of 5, over the SAME 40 p6group
// cases as the individual (n=1) and grouped-40 (n=40) arms.
//
// This is NOT the 8-agent fan-out (scripts/p6group_micro_workflow.js) — that one
// pays the 36k-token corpus read eight times. Here the corpus is read once and
// the batches are fed in one group file at a time, so case text enters context
// incrementally instead of all 40 up front.
//
// Against grouped-40 the ONLY difference is batch size: 8x5 instead of 4x10.
// grouped-40 emitted 29k output tokens on its first write, which invalidated a
// 137k-token cached prefix (turn 10: cache_creation 136,941 / cache_read 0).
// Halving the batch should halve per-response output; whether that avoids the
// blowout, and whether it restores citation density, is what this measures.
//
//   Workflow({ scriptPath: "scripts/p6group_piped_workflow.js" })
//   uv run python scripts/agent_meter.py "one batch at a time"=piped-5=40
//   uv run python scripts/p6group_report.py

export const meta = {
  name: 'p6group-piped-5',
  description: 'Decide 40 p6group cases in one agent, 8 piped batches of 5',
  phases: [{ title: 'Decide', detail: '1 agent, 8 batches x 5 cases', model: 'opus' }],
}

const G = '/Users/mgyk/mooracle/aidag/data/interim/p6group'
const N = 5
const GROUPS = ['g-00', 'g-01', 'g-02', 'g-03', 'g-04', 'g-05', 'g-06', 'g-07']

const RECEIPT = {
  type: 'object',
  properties: {
    written: { type: 'number' },
    batches: { type: 'number' },
  },
  required: ['written', 'batches'],
  additionalProperties: false,
}

// Deciding rules are copied verbatim from the grouped-40 prompt so the arms stay
// comparable; only the batching instruction differs.
const prompt =
  `You decide how a Swedish party SHOULD vote in ${GROUPS.length * N} SEPARATE Riksdag divisions, and write each decision to a file. You work through them ONE BATCH AT A TIME.\n\n` +
  `SETUP (once):\n` +
  `1. Read ${G}/system/M.txt — your role, rules and the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read ${G}/schema.json — every decision must conform to it.\n\n` +
  `THEN, for each of these ${GROUPS.length} batch files IN ORDER:\n` +
  GROUPS.map((g) => `   ${G}/g5/${g}.json`).join('\n') + '\n\n' +
  `For each batch file: read it (its "cases" array holds ${N} cases, each with "cid" and "user"), decide all ${N} cases, and IMMEDIATELY append those ${N} decisions as JSON Lines to ${G}/out_g5p/<batch name>.jsonl (e.g. g-00.json -> g-00.jsonl). Then move to the next batch file. Do NOT read ahead, and do NOT hold decisions back to write them all at the end.\n\n` +
  `Deciding — rules:\n` +
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. Do NOT seek consistency across cases and do NOT let one decision influence another; the same commitment can point one way in one division and the other way in another.\n` +
  `- Base each decision ONLY on the documents in the role file and that case's own text.\n` +
  `- Do NOT use knowledge of how the party actually voted, news, or later events.\n` +
  `- Do NOT weigh in party tactics, coalition loyalty or how other parties vote.\n` +
  `- Quotes in "citations" MUST be verbatim substrings of the documents in the role file.\n` +
  `- Uncertainty is valid: use confidence="low" / coverage="not_covered" when the plan does not reach the case. Always give at least one citation.\n` +
  `- Fields: hallning ("stodjer"|"avvisar" — the plan's stance on what the MOTFORSLAG demands), confidence, coverage, motivering (Swedish, 2-4 sentences), citations [{document,quote,princip}], omvarld {paverkar,faktorer}, flags, plan_tacker_utskottets_skal ("ja"|"nej", answered independently of hallning).\n` +
  `- Each written line is one JSON object conforming to the schema PLUS a "cid" field copied exactly from the batch file.\n\n` +
  `When all ${GROUPS.length} batches are written, reply via the structured output with the total decisions written and the number of batches.`

phase('Decide')
log(`piped arm: 1 agent, ${GROUPS.length} batches x ${N} cases = ${GROUPS.length * N} decisions`)

const r = await agent(prompt, {
  label: `piped ${GROUPS.length}x${N}`,
  phase: 'Decide',
  model: 'opus',
  agentType: 'general-purpose', // needs Read + Write
  schema: RECEIPT,
})

log(r ? `done: agent claims ${r.written} decisions across ${r.batches} batches`
      : 'agent errored — nothing to merge')
return r ?? { written: 0, batches: 0 }
