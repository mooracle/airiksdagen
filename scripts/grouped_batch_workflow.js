// THE workflow for the full run: one agent decides a GROUP of same-party,
// same-month cases, reading that party's corpus once. The corpus is the
// expensive part (53k-126k tokens under p5), so group size is what makes the
// run affordable — ~19k tokens/decision at group 8, ~8k at month scale.
//
//   Workflow({ scriptPath: "scripts/grouped_batch_workflow.js",
//              args: { manifestPath: "<abs path to batch-NNN.json>",
//                      model: "opus" } })     // model optional, default sonnet
//
// Manifest items must be kind 'simgroup' (aidag agent-prepare --group N, or
// --group 0 for size-driven packing). The citable-document set comes from the
// manifest's prompt_version. Returns {run_id, sims, probes} — `aidag
// agent-ingest` works unchanged.

export const meta = {
  name: 'aidag-grouped-batch',
  description: 'One batch of AI-party vote decisions, grouped by party-month',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Simulate', detail: 'one agent per (party x case-group)' },
    { title: 'Probe', detail: 'memorization probes (one per case)' },
  ],
}

const PARTY_CODES = ['S', 'M', 'SD', 'C', 'V', 'KD', 'MP', 'L']

const PROBE_SCHEMA = {
  type: 'object',
  properties: {
    recalls_case: { type: 'boolean' },
    positions: {
      type: 'object',
      properties: Object.fromEntries(
        PARTY_CODES.map((p) => [p, { type: 'string', enum: ['Ja', 'Nej', 'Avstår', 'Frånvarande', 'okänt'] }]),
      ),
      required: PARTY_CODES,
      additionalProperties: false,
    },
    notes: { type: 'string' },
  },
  required: ['recalls_case', 'positions', 'notes'],
  additionalProperties: false,
}

// Citable documents, per prompt version — must mirror corpus.DOCS_P4/DOCS_P5.
// Derived from the manifest, never a permissive union: a p4 agent that never saw
// a partiprogram must not even be able to name one.
const DOCS_BY_VERSION = {
  p4: ['valmanifest', 'tidoavtalet'],
  p5: ['valmanifest', 'tidoavtalet', 'partiprogram', 'budgetmotion'],
}

const decisionProps = (promptVersion) => ({
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
        document: {
          type: 'string',
          enum: DOCS_BY_VERSION[promptVersion] || DOCS_BY_VERSION.p4,
        },
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
})

const groupSchema = (promptVersion) => {
  const props = decisionProps(promptVersion)
  return {
    type: 'object',
    properties: {
      decisions: {
        type: 'array',
        items: {
          type: 'object',
          properties: props,
          required: Object.keys(props),
          additionalProperties: false,
        },
      },
    },
    required: ['decisions'],
    additionalProperties: false,
  }
}

// Compact manifest: per-case vid and case_file derive from the cid and the
// manifest-level cases_dir — keeps the loader agent's transcription small
// enough for its output-token limit even on large batches.
const MANIFEST_SCHEMA = {
  type: 'object',
  properties: {
    run_id: { type: 'string' },
    prompt_version: { type: 'string' },
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
          kind: { type: 'string', enum: ['simgroup', 'probe'] },
          party: { type: 'string' },
          sys: { type: 'string' },
          cids: { type: 'array', items: { type: 'string' } },
          vid: { type: 'string' },
        },
        required: ['kind'],
        additionalProperties: false,
      },
    },
  },
  // prompt_version is REQUIRED: it decides which documents an agent may cite, so
  // a loader that quietly omitted it would leave the schema to guess.
  required: [
    'run_id', 'prompt_version', 'n_sims', 'n_probes',
    'cases_dir', 'probes_dir', 'system_dir', 'items',
  ],
  additionalProperties: false,
}

// Fail fast if args were not forwarded; tolerate JSON-encoded string args.
if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch { /* falls through to the guard below */ }
}
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('agentrun')) {
  throw new Error(`grouped_batch_workflow: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}
// Vote/probe model. One model per run — mixing them would make the per-party
// statistics incomparable across batches of the same dataset.
const MODEL = args.model || 'sonnet'
if (!['sonnet', 'opus', 'haiku'].includes(MODEL)) {
  throw new Error(`grouped_batch_workflow: unknown model ${JSON.stringify(MODEL)}`)
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
const groups = manifest.items
  .filter((i) => i.kind === 'simgroup')
  .map((item) => ({
    ...item,
    system_file: `${manifest.system_dir}/${item.sys}`,
    cases: item.cids.map((cid) => {
      const vid = cid.split(':')[1] ?? ''
      return { cid, vid, case_file: `${manifest.cases_dir}/${vid}.json` }
    }),
  }))
const probeItems = manifest.items
  .filter((i) => i.kind === 'probe')
  .map((i) => ({ vid: i.vid, path: `${manifest.probes_dir}/${i.vid}.json` }))

// verify the manifest survived the loader agent's transcription
const PARTY_SET = new Set(PARTY_CODES)
const problems = []
const totalCases = groups.reduce((s, i) => s + i.cases.length, 0)
if (totalCases !== manifest.n_sims) problems.push(`case count ${totalCases} != manifest n_sims ${manifest.n_sims}`)
if (probeItems.length !== manifest.n_probes) problems.push(`probe count ${probeItems.length} != manifest n_probes ${manifest.n_probes}`)
for (const item of probeItems) {
  if (!item.vid) problems.push('probe item missing vid')
}
for (const dir of [manifest.cases_dir, manifest.probes_dir, manifest.system_dir]) {
  if (!dir || !dir.includes('agentrun')) problems.push(`bad manifest dir: ${dir}`)
}
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
// The citable-document set is decided by the prompt version, so a mis-read
// version would let an agent cite a document it never saw. Fail rather than
// silently fall back.
const promptVersion = manifest.prompt_version
if (!DOCS_BY_VERSION[promptVersion]) {
  problems.push(`unknown prompt_version ${JSON.stringify(promptVersion)} (expected one of ${Object.keys(DOCS_BY_VERSION).join(', ')})`)
}
if (problems.length) {
  throw new Error(`manifest failed integrity check (${problems.length} problems):\n${problems.slice(0, 10).join('\n')}`)
}
const GROUP_SCHEMA = groupSchema(promptVersion)
log(`batch loaded: ${groups.length} groups (${totalCases} decisions), ${probeItems.length} probes (run ${manifest.run_id}, prompt ${promptVersion}, model ${MODEL}, citable: ${DOCS_BY_VERSION[promptVersion].join('/')})`)

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

const probePrompt = (item) =>
  `Read the JSON file at ${item.path}. Its "prompt" field contains instructions and a question ` +
  `about a real Riksdag vote. Follow them exactly: answer only from your actual memory of this ` +
  `specific vote; if you do not remember it, set recalls_case=false and "okänt" for parties you ` +
  `do not remember. Read only that one file, use no other tools, answer only via structured output.`

const byCid = new Map(groups.flatMap((item) => item.cases.map((c) => [c.cid, { party: item.party, vid: c.vid }])))
const work = [
  ...groups.map((item) => () =>
    agent(groupPrompt(item), {
      label: `${item.party}:${item.cases.length} cases`,
      phase: 'Simulate',
      model: MODEL,
      schema: GROUP_SCHEMA,
    }).then((r) => (r ? { kind: 'group', decisions: r.decisions } : null))),
  ...probeItems.map((item) => () =>
    agent(probePrompt(item), { label: `probe:${item.vid}`, phase: 'Probe', model: MODEL, schema: PROBE_SCHEMA })
      .then((r) => (r ? { kind: 'probe', vid: item.vid, result: r } : null))),
]
const results = (await parallel(work)).filter(Boolean)

const sims = []
let unknown = 0
for (const r of results.filter((r) => r.kind === 'group')) {
  for (const d of r.decisions) {
    const ref = byCid.get(d.cid)
    if (!ref) { unknown++; continue } // hallucinated cid — leave the real one pending
    const { cid, ...decision } = d
    sims.push({ kind: 'sim', cid, party: ref.party, vid: ref.vid, decision })
  }
}
const probes = results.filter((r) => r.kind === 'probe').map(({ vid, result }) => ({ kind: 'probe', vid, result }))
if (unknown > 0) log(`WARNING: ${unknown} decisions returned with unknown cids (dropped, stay pending)`)
log(`batch done: ${sims.length}/${totalCases} decisions, ${probes.length}/${probeItems.length} probes`)
return { run_id: manifest.run_id, sims, probes }
