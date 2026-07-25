// Group-size sweep arm: 8 agents x 5 cases, over the SAME 40 p6group cases the
// individual (n=1) and grouped-40 (n=40) arms already decided. The prompt is the
// grouped-40 prompt verbatim except for the case count, the group file and the
// out file — so the only variable across the three arms is group size.
//
//   Workflow({ scriptPath: "scripts/p6group_micro_workflow.js" })
//
// Meter it afterwards with:
//   uv run python scripts/agent_meter.py "p6group/g5/"=micro-5=40
//   uv run python scripts/p6group_report.py

export const meta = {
  name: 'p6group-micro-5',
  description: 'Decide 40 p6group cases as 8 groups of 5 (group-size sweep arm)',
  phases: [{ title: 'Decide', detail: '8 agents, 5 cases each', model: 'opus' }],
}

const G = '/Users/mgyk/mooracle/aidag/data/interim/p6group'
const N = 5
const GROUPS = ['g-00', 'g-01', 'g-02', 'g-03', 'g-04', 'g-05', 'g-06', 'g-07']

const RECEIPT = {
  type: 'object',
  properties: {
    written: { type: 'number' },
    cids: { type: 'array', items: { type: 'string' } },
  },
  required: ['written', 'cids'],
  additionalProperties: false,
}

const prompt = (g) =>
  `You decide how a Swedish party SHOULD vote in ${N} SEPARATE Riksdag divisions, and write each decision to a file.\n\n` +
  `1. Read ${G}/system/M.txt — your role, rules and the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read ${G}/g5/${g}.json — its "cases" array holds all ${N} cases, each with a "cid" and a "user" field.\n` +
  `3. Read ${G}/schema.json — every decision must conform to it.\n\n` +
  `Deciding — rules:\n` +
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. Do NOT seek consistency across cases and do NOT let one decision influence another; the same commitment can point one way in one division and the other way in another.\n` +
  `- Base each decision ONLY on the documents in the role file and that case's own text.\n` +
  `- Do NOT use knowledge of how the party actually voted, news, or later events.\n` +
  `- Do NOT weigh in party tactics, coalition loyalty or how other parties vote.\n` +
  `- Quotes in "citations" MUST be verbatim substrings of the documents in the role file.\n` +
  `- Uncertainty is valid: use confidence="low" / coverage="not_covered" when the plan does not reach the case. Always give at least one citation.\n` +
  `- Fields: hallning ("stodjer"|"avvisar" — the plan's stance on what the MOTFORSLAG demands), confidence, coverage, motivering (Swedish, 2-4 sentences), citations [{document,quote,princip}], omvarld {paverkar,faktorer}, flags, plan_tacker_utskottets_skal ("ja"|"nej", answered independently of hallning).\n\n` +
  `Writing: append decisions as JSON Lines to ${G}/out_g5/${g}.jsonl. Each line is one JSON object conforming to the schema PLUS a "cid" field copied exactly from the group file.\n` +
  `Cover all ${N} cases. Reply via the structured output with the count and the cids you wrote.`

phase('Decide')
log(`micro-batch arm: ${GROUPS.length} agents x ${N} cases = ${GROUPS.length * N} decisions`)

// One stage, so pipeline() and parallel() are equivalent here — pipeline is used
// because it is the shape a verify stage would slot into without a barrier.
const results = await pipeline(GROUPS, (g) =>
  agent(prompt(g), {
    label: `${g}:${N} cases`,
    phase: 'Decide',
    model: 'opus',
    agentType: 'general-purpose', // needs the Write tool
    schema: RECEIPT,
  }).then((r) => ({ g, written: r?.written ?? 0, error: !r })))

const ok = results.filter(Boolean)
const claimed = ok.reduce((s, r) => s + r.written, 0)
const failed = ok.filter((r) => r.error).length
if (failed) log(`WARNING: ${failed}/${GROUPS.length} agents errored`)
log(`done: agents claim ${claimed}/${GROUPS.length * N} decisions in ${G}/out_g5`)
return { groups: ok, claimed }
