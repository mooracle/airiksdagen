// Grouped arm with the citation-targeted prompt, parameterised by group size.
//
//   Workflow({ scriptPath: "scripts/p6group_cited_workflow.js",
//              args: { n: 60, chunk: 10, group: "group60.json", out: "out_g60" } })
//
// The prompt is the one validated at n=30: it asks for 3-4 citations ordered
// decisive-first instead of the old "at least one" floor, which is what closed
// the density gap (2.47 -> 3.90 citations/decision, matching one-by-one's 3.87).
//
// chunk defaults to 10. The n=30 run tried chunk=5 to avoid grouped-40's cache
// blowout and got TWO blowouts instead of one (turns 9 and 11, 141k and 165k
// rewritten at 1.25x against zero cache reads), so smaller write blocks are not
// the lever — 10 is the default again.

export const meta = {
  name: 'p6group-cited',
  description: 'Decide a p6group case set in one agent, citation-targeted prompt',
  phases: [{ title: 'Decide', detail: 'one grouped agent' }],
}

if (typeof args === 'string') { try { args = JSON.parse(args) } catch { /* guarded below */ } }
// dir = base directory holding system/, group file, schema.json, out dir.
// sys = system-file name inside dir/system/. Both default to the p6group party-M
// setup so existing invocations keep working; the validation sample overrides
// them to point at data/interim/p6val/<party><year>/.
const G = args?.dir ?? '/Users/mgyk/mooracle/aidag/data/interim/p6group'
const SYS = args?.sys ?? 'M.txt'
const N = args?.n
const CHUNK = args?.chunk ?? 10
const GROUP = args?.group
const OUT = args?.out
const CITES = args?.cites ?? 'many'
// model: a tier alias ('opus' = whatever the session's Opus is) or an explicit
// id ('claude-opus-5'). The alias is NOT stable across sessions — the arms
// metered as "Opus 4.8" in the study were run with 'opus' when that was the
// session model, so pin the id whenever the run is meant to be comparable.
const MODEL = args?.model ?? 'opus'
// effort: undefined = inherit the session effort. Named explicitly, it is the
// thinking-depth / token-spend dial (low|medium|high|xhigh|max).
const EFFORT = args?.effort
if (!N || !GROUP || !OUT) {
  throw new Error(`p6group_cited_workflow: need args {n, group, out}; got ${JSON.stringify(args)}`)
}
if (!['many', 'few'].includes(CITES)) {
  throw new Error(`p6group_cited_workflow: cites must be 'many' or 'few'; got ${CITES}`)
}
if (!['opus', 'sonnet', 'haiku'].includes(MODEL) && !MODEL.startsWith('claude-')) {
  throw new Error(`p6group_cited_workflow: unknown model ${MODEL}`)
}
if (EFFORT && !['low', 'medium', 'high', 'xhigh', 'max'].includes(EFFORT)) {
  throw new Error(`p6group_cited_workflow: unknown effort ${EFFORT}`)
}
// Haiku's context window is 200K against Opus/Sonnet's 1M, and the Opus n=20
// runs peaked at 167-184k. A haiku run at n>=20 is therefore close to the
// ceiling — if it truncates or errors, that IS the result, not a bug.

// Two citation regimes over an identical corpus, case set and schema.
//
// 'many' (3-4) closed the density gap to one-by-one (2.47 -> 3.95) but bought
// nothing in targeting: agreement with the one-by-one decisive citation stayed
// at ~45%, which is the reproducibility floor — two runs of the SAME prompt
// agree on the decisive citation only 60% of the time. The extra citations are
// real (100% name a new princip, spread over ~37% of the corpus), just not
// better aimed.
//
// 'few' (1-2) tests the other side: keep the broad SEARCH that the many-regime
// forces, but report only the strongest result. If targeting is a product of
// searching widely rather than of citing widely, this should match 'many' on
// decisive-citation agreement while emitting far fewer output tokens.
const CITATION_RULES = CITES === 'many' ? (
  `CITATIONS — this is the part that matters most, and the part most often done thinly:\n` +
  `- Give 3 to 4 citations for a typical decision. Drop to 1-2 ONLY when the plan genuinely touches this case in just one or two places; if you find yourself writing a single citation repeatedly, you are under-reading the documents.\n` +
  `- Order them by importance: the FIRST citation must be the commitment that actually carries the vote, not a generic statement of values. Later citations add supporting or qualifying commitments.\n` +
  `- Each "quote" MUST be a verbatim substring of the documents in the role file — copy it exactly, do not paraphrase, do not repair grammar, do not join text across a gap. Prefer the specific passage over the general one.\n` +
  `- Give each citation a short "princip" (2-6 Swedish words) naming the commitment it encodes, e.g. "minskad asylinvandring".\n` +
  `- Do not pad: a fourth citation that repeats the third adds nothing. Distinct commitments only.\n\n`
) : (
  `CITATIONS — few, but the RIGHT ones. Precision is the whole job here:\n` +
  `- Before you choose, SEARCH WIDELY. Scan the party's documents for every passage that could bear on this division, and hold the candidates side by side. Do not settle on the first plausible match you encounter — the best passage is often not the first one you find.\n` +
  `- Then report only the strongest: give exactly 1 citation normally. Give a 2nd ONLY when a genuinely separate commitment independently bears on the vote and would change the reasoning if it were absent.\n` +
  `- The citation you keep must be the passage that ACTUALLY DECIDES the vote — the specific, operative commitment. Reject generic statements of values, mission language, and preamble; if your quote would fit a dozen unrelated divisions, you have picked the wrong one.\n` +
  `- Prefer the narrowest passage that still carries the decision. A precise sentence beats a broad paragraph.\n` +
  `- The "quote" MUST be a verbatim substring of the documents in the role file — copy it exactly, do not paraphrase, do not repair grammar, do not join text across a gap.\n` +
  `- Give it a short "princip" (2-6 Swedish words) naming the commitment it encodes, e.g. "minskad asylinvandring".\n` +
  `- Never cite more than 2. Breadth is not the goal; being right is.\n\n`
)

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
  `1. Read ${G}/system/${SYS} — your role, rules and the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read ${G}/${GROUP} — its "cases" array holds all ${N} cases, each with a "cid" and a "user" field.\n` +
  `3. Read ${G}/schema.json — every decision must conform to it.\n\n` +
  `Deciding — rules:\n` +
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. Do NOT seek consistency across cases and do NOT let one decision influence another; the same commitment can point one way in one division and the other way in another.\n` +
  `- Base each decision ONLY on the documents in the role file and that case's own text.\n` +
  `- Do NOT use knowledge of how the party actually voted, news, or later events.\n` +
  `- Do NOT weigh in party tactics, coalition loyalty or how other parties vote.\n` +
  `- Uncertainty is valid: use confidence="low" / coverage="not_covered" when the plan does not reach the case. Judge coverage honestly — "explicit" means the plan speaks directly to what this division decides, not merely to the policy area.\n\n` +
  CITATION_RULES +
  `Fields: hallning ("stodjer"|"avvisar" — the plan's stance on what the MOTFORSLAG demands), confidence, coverage, motivering (Swedish, 2-4 sentences), citations [{document,quote,princip}], omvarld {paverkar,faktorer}, flags, plan_tacker_utskottets_skal ("ja"|"nej", answered independently of hallning).\n\n` +
  `Writing: append decisions as JSON Lines to ${G}/${OUT}/decisions.jsonl, in BLOCKS OF ${CHUNK} cases. Write each block before deciding the next one — do not hold decisions back to the end. Each line is one JSON object conforming to the schema PLUS a "cid" field copied exactly from the group file. No markdown fences, no commentary in the file.\n` +
  `Cover all ${N} cases. Reply via the structured output with the number written and your mean citations per decision.`

phase('Decide')
log(`grouped-${N} on ${MODEL}${EFFORT ? `/${EFFORT}` : ''} (cites=${CITES}): `
    + `${N} cases, blocks of ${CHUNK} -> ${OUT}`)

const r = await agent(prompt, {
  label: `${MODEL}${EFFORT ? `-${EFFORT}` : ''}-${N}-${CITES}`,
  phase: 'Decide',
  model: MODEL,
  ...(EFFORT ? { effort: EFFORT } : {}),
  agentType: 'general-purpose', // needs Read + Write
  schema: RECEIPT,
})

log(r ? `done: ${r.written}/${N} decisions, agent reports ${r.mean_citations} citations/decision`
      : 'agent errored — nothing written')
return r ?? { written: 0, mean_citations: 0 }
