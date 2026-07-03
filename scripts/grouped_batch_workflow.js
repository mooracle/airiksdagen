// Grouped variant of agent_batch_workflow.js: one Sonnet agent decides a
// GROUP of same-party, same-month cases, reading the party corpus once
// (~4x fewer tokens per decision). Used for the grouped-vs-per-case
// methodology experiment; do not use for the headline run unless the
// comparison (aidag compare-runs) shows equivalence.
//   Workflow({ scriptPath: "scripts/grouped_batch_workflow.js",
//              args: { manifestPath: "<abs path to batch-NNN.json>" } })
// Manifest items must be kind 'simgroup' (aidag agent-prepare --group N).
// Returns the same shape as the per-case script ({run_id, sims, probes}),
// so `aidag agent-ingest` works unchanged.

export const meta = {
  name: 'aidag-grouped-batch',
  description: 'One batch of AI-party vote decisions, grouped N cases per agent (Sonnet)',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Simulate', detail: 'one Sonnet agent per (party x case-group)', model: 'sonnet' },
  ],
}

const PARTY_CODES = ['S', 'M', 'SD', 'C', 'V', 'KD', 'MP', 'L']

const DECISION_PROPS = {
  cid: { type: 'string' },
  rost: { type: 'string', enum: ['Ja', 'Nej', 'Avstår'] },
  confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  coverage: { type: 'string', enum: ['explicit', 'inferred', 'not_covered'] },
  motivering: { type: 'string' },
  citations: {
    type: 'array',
    items: {
      type: 'object',
      properties: {
        document: { type: 'string', enum: ['valmanifest', 'tidoavtalet'] },
        quote: { type: 'string' },
        princip: { type: 'string' },
      },
      required: ['document', 'quote', 'princip'],
      additionalProperties: false,
    },
  },
  omvarld: {
    type: 'object',
    properties: {
      paverkar: { type: 'boolean' },
      faktorer: {
        type: 'array',
        items: {
          type: 'object',
          properties: { faktor: { type: 'string' }, effekt: { type: 'string' } },
          required: ['faktor', 'effekt'],
          additionalProperties: false,
        },
      },
    },
    required: ['paverkar', 'faktorer'],
    additionalProperties: false,
  },
  flags: { type: 'array', items: { type: 'string' } },
}

const GROUP_SCHEMA = {
  type: 'object',
  properties: {
    decisions: {
      type: 'array',
      items: {
        type: 'object',
        properties: DECISION_PROPS,
        required: Object.keys(DECISION_PROPS),
        additionalProperties: false,
      },
    },
  },
  required: ['decisions'],
  additionalProperties: false,
}

// Compact manifest: per-case vid and case_file derive from the cid and the
// manifest-level cases_dir — keeps the loader agent's transcription small
// enough for its output-token limit even on large batches.
const MANIFEST_SCHEMA = {
  type: 'object',
  properties: {
    run_id: { type: 'string' },
    n_sims: { type: 'number' },
    n_probes: { type: 'number' },
    cases_dir: { type: 'string' },
    probes_dir: { type: 'string' },
    system_dir: { type: 'string' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          kind: { type: 'string', enum: ['simgroup'] },
          party: { type: 'string' },
          sys: { type: 'string' },
          cids: { type: 'array', items: { type: 'string' } },
        },
        required: ['kind', 'party', 'sys', 'cids'],
        additionalProperties: false,
      },
    },
  },
  required: ['run_id', 'n_sims', 'n_probes', 'cases_dir', 'system_dir', 'items'],
  additionalProperties: false,
}

// Fail fast if args were not forwarded; tolerate JSON-encoded string args.
if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch { /* falls through to the guard below */ }
}
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('agentrun')) {
  throw new Error(`grouped_batch_workflow: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}

phase('Load')
const manifest = await agent(
  `Read the file ${args.manifestPath} and return its exact contents as structured output. Do not modify anything.`,
  { label: 'load-manifest', model: 'sonnet', schema: MANIFEST_SCHEMA },
)
if (!manifest) {
  throw new Error('manifest loader agent returned null (agent error) — nothing was run; relaunch the workflow')
}

// derive per-case fields from the compact manifest
const groups = manifest.items.map((item) => ({
  ...item,
  system_file: `${manifest.system_dir}/${item.sys}`,
  cases: item.cids.map((cid) => {
    const vid = cid.split(':')[1] ?? ''
    return { cid, vid, case_file: `${manifest.cases_dir}/${vid}.json` }
  }),
}))

// verify the manifest survived the loader agent's transcription
const PARTY_SET = new Set(PARTY_CODES)
const problems = []
const totalCases = groups.reduce((s, i) => s + i.cases.length, 0)
if (totalCases !== manifest.n_sims) problems.push(`case count ${totalCases} != manifest n_sims ${manifest.n_sims}`)
if (!manifest.cases_dir || !manifest.cases_dir.includes('agentrun')) problems.push(`bad cases_dir: ${manifest.cases_dir}`)
for (const item of groups) {
  if (!item.sys || !PARTY_SET.has(item.party)) {
    problems.push(`bad group item: ${item.party} / ${item.sys}`)
    continue
  }
  for (const c of item.cases) {
    const [party, vid, version, arm] = c.cid.split(':')
    if (party !== item.party || !vid || !version || !arm) {
      problems.push(`malformed cid in group ${item.party}: ${c.cid}`)
    }
  }
}
if (problems.length) {
  throw new Error(`manifest failed integrity check (${problems.length} problems):\n${problems.slice(0, 10).join('\n')}`)
}
log(`batch loaded: ${groups.length} groups, ${totalCases} decisions (run ${manifest.run_id})`)

const groupPrompt = (item) =>
  `You decide how a Swedish party SHOULD vote in ${item.cases.length} SEPARATE Riksdag divisions, strictly from the party's own documents.\n\n` +
  `1. Read the file ${item.system_file} — it is your complete role and instructions, including the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read each case file below. Each JSON file's "user" field is one case to decide:\n` +
  item.cases.map((c, i) => `   ${i + 1}. [cid ${c.cid}] ${c.case_file}`).join('\n') + '\n\n' +
  `Rules:\n` +
  `- Read ONLY those files. No web search, no other files, no other tools.\n` +
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. ` +
  `Do NOT seek consistency across the cases in this batch and do NOT let one decision influence another; ` +
  `the same commitment can point to Ja in one division and Nej in another depending on what is actually being voted on.\n` +
  `- Base each decision exclusively on the documents in the role file and the situational picture in that case's text. Do NOT use your own knowledge of how the party actually voted or of events after the stated time.\n` +
  `- "motivering": Swedish, SHORT — 2-4 sentences (max ~80 words) stating the decisive plan commitment and how it maps to Ja/Nej/Avstår.\n` +
  `- Every citation "quote" must be a VERBATIM substring copied exactly from the document text. ` +
  `Pick the SPECIFIC passage that actually carries the vote, not a generic statement. Order ` +
  `citations by importance — the FIRST one is the decisive commitment. Give each citation a ` +
  `short "princip" (2-6 Swedish words) naming the commitment.\n` +
  `- Each case text includes a worldstate block — weigh it in ONLY when it materially changes how ` +
  `the documents apply (set omvarld.paverkar=true, max 3 factors; otherwise false and empty list). ` +
  `If the documents are silent and worldstate decides, use coverage="not_covered" AND omvarld.paverkar=true.\n` +
  `- Return one decision per case, copying its "cid" EXACTLY as given above. Answer only via the structured output.`

phase('Simulate')
const byCid = new Map(groups.flatMap((item) => item.cases.map((c) => [c.cid, { party: item.party, vid: c.vid }])))
const results = (
  await parallel(
    groups.map((item) => () =>
      agent(groupPrompt(item), {
        label: `${item.party}:${item.cases.length} cases`,
        phase: 'Simulate',
        model: 'sonnet',
        schema: GROUP_SCHEMA,
      }).then((r) => (r ? { item, decisions: r.decisions } : null))),
  )
).filter(Boolean)

const sims = []
let unknown = 0
for (const { decisions } of results) {
  for (const d of decisions) {
    const ref = byCid.get(d.cid)
    if (!ref) { unknown++; continue } // hallucinated cid — leave the real one pending
    const { cid, ...decision } = d
    sims.push({ kind: 'sim', cid, party: ref.party, vid: ref.vid, decision })
  }
}
if (unknown > 0) log(`WARNING: ${unknown} decisions returned with unknown cids (dropped, stay pending)`)
log(`batch done: ${sims.length}/${totalCases} decisions from ${results.length}/${groups.length} groups`)
return { run_id: manifest.run_id, sims, probes: [] }
