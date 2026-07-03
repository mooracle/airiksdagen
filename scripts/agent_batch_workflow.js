// Workflow script for one batch of the full run (see docs/orchestration-full-run.md).
// Invoke from the Opus orchestrator session with:
//   Workflow({ scriptPath: "scripts/agent_batch_workflow.js",
//              args: { manifestPath: "<abs path to batch-NNN.json>" } })
// Every vote/probe agent runs on Sonnet. Agents that die or get skipped return
// null and are simply left pending — the next `aidag agent-prepare` re-issues them.

export const meta = {
  name: 'aidag-agent-batch',
  description: 'One checkpointed batch of AI-party vote decisions + probes (Sonnet agents)',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Simulate', detail: 'one Sonnet agent per (case x party)', model: 'sonnet' },
    { title: 'Probe', detail: 'memorization probes', model: 'sonnet' },
  ],
}

const DECISION_SCHEMA = {
  type: 'object',
  properties: {
    rost: { type: 'string', enum: ['Ja', 'Nej', 'Avstår'] },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    coverage: { type: 'string', enum: ['explicit', 'inferred', 'not_covered'] },
    motivering: { type: 'string' },
    citations: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          // only documents that are actually in the agent's context — citing
          // anything else is uncheckable and therefore not allowed by schema
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
  },
  required: ['rost', 'confidence', 'coverage', 'motivering', 'citations', 'omvarld', 'flags'],
  additionalProperties: false,
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

const MANIFEST_SCHEMA = {
  type: 'object',
  properties: {
    run_id: { type: 'string' },
    n_sims: { type: 'number' },
    n_probes: { type: 'number' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          kind: { type: 'string', enum: ['sim', 'probe'] },
          cid: { type: 'string' },
          party: { type: 'string' },
          vid: { type: 'string' },
          system_file: { type: 'string' },
          case_file: { type: 'string' },
          path: { type: 'string' },
        },
        required: ['kind', 'vid'],
        additionalProperties: false,
      },
    },
  },
  required: ['run_id', 'n_sims', 'n_probes', 'items'],
  additionalProperties: false,
}

// Fail fast if args were not forwarded — never let the loader agent guess
// which manifest to run (240 unintended agents cost more than one error).
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('agentrun')) {
  throw new Error(`agent_batch_workflow: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}

phase('Load')
const manifest = await agent(
  `Read the file ${args.manifestPath} and return its exact contents as structured output. Do not modify anything.`,
  { label: 'load-manifest', model: 'sonnet', schema: MANIFEST_SCHEMA },
)
const simItems = manifest.items.filter((i) => i.kind === 'sim')
const probeItems = manifest.items.filter((i) => i.kind === 'probe')

// The manifest passes through an agent transcription above — verify it arrived
// intact before spending ~240 agents on it. A dropped item, a truncated path or
// a typo'd cid would otherwise surface as corrupt or missing results at ingest.
const PARTY_SET = new Set(PARTY_CODES)
const problems = []
if (simItems.length !== manifest.n_sims) problems.push(`sim count ${simItems.length} != manifest n_sims ${manifest.n_sims}`)
if (probeItems.length !== manifest.n_probes) problems.push(`probe count ${probeItems.length} != manifest n_probes ${manifest.n_probes}`)
for (const item of simItems) {
  if (!item.cid || !item.party || !item.system_file || !item.case_file) {
    problems.push(`incomplete sim item: ${JSON.stringify(item)}`)
    continue
  }
  const [party, vid] = item.cid.split(':')
  if (party !== item.party || vid !== item.vid || !PARTY_SET.has(item.party)) {
    problems.push(`cid does not match item fields: ${item.cid} vs ${item.party}/${item.vid}`)
  }
}
for (const item of probeItems) {
  if (!item.path) problems.push(`probe item missing path: ${JSON.stringify(item)}`)
}
if (problems.length) {
  throw new Error(`manifest failed integrity check (${problems.length} problems):\n${problems.slice(0, 10).join('\n')}`)
}
log(`batch loaded: ${simItems.length} decisions, ${probeItems.length} probes (run ${manifest.run_id})`)

const simPrompt = (item) =>
  `You decide how a Swedish party SHOULD vote in one Riksdag division, strictly from the party's own documents.\n\n` +
  `1. Read the file ${item.system_file} — it is your complete role and instructions, including the party's documents.\n` +
  `2. Read the JSON file ${item.case_file} — its "user" field is the case to decide.\n\n` +
  `Rules:\n` +
  `- Read ONLY those two files. No web search, no other files, no other tools.\n` +
  `- Base the decision exclusively on the documents in the role file and the situational picture in the case text. Do NOT use your own knowledge of how the party actually voted or of events after the stated time.\n` +
  `- "motivering": Swedish, SHORT — 2-4 sentences (max ~80 words) stating the decisive plan commitment and how it maps to Ja/Nej/Avstår.\n` +
  `- Every citation "quote" must be a VERBATIM substring copied exactly from the document text. ` +
  `Pick the SPECIFIC passage that actually carries the vote, not a generic statement. Order ` +
  `citations by importance — the FIRST one is the decisive commitment. Give each citation a ` +
  `short "princip" (2-6 Swedish words) naming the commitment, e.g. "minskad asylinvandring".\n` +
  `- The case text includes a worldstate block (economy + recent events) — incoming reality the ` +
  `party could not plan for. The documents remain the basis; weigh worldstate in ONLY when it ` +
  `materially changes how they apply. If you do, set omvarld.paverkar=true with max 3 factors; ` +
  `otherwise paverkar=false with an empty list. If the documents are silent and worldstate ` +
  `decides, use coverage="not_covered" AND omvarld.paverkar=true.\n` +
  `- Answer only via the structured output.`

const probePrompt = (item) =>
  `Read the JSON file at ${item.path}. Its "prompt" field contains instructions and a question ` +
  `about a real Riksdag vote. Follow them exactly: answer only from your actual memory of this ` +
  `specific vote; if you do not remember it, set recalls_case=false and "okänt" for parties you ` +
  `do not remember. Read only that one file, use no other tools, answer only via structured output.`

const work = [
  ...simItems.map((item) => () =>
    agent(simPrompt(item), { label: item.cid, phase: 'Simulate', model: 'sonnet', schema: DECISION_SCHEMA })
      .then((d) => (d ? { kind: 'sim', cid: item.cid, party: item.party, vid: item.vid, decision: d } : null))),
  ...probeItems.map((item) => () =>
    agent(probePrompt(item), { label: `probe:${item.vid}`, phase: 'Probe', model: 'sonnet', schema: PROBE_SCHEMA })
      .then((r) => (r ? { kind: 'probe', vid: item.vid, result: r } : null))),
]

const results = (await parallel(work)).filter(Boolean)
const sims = results.filter((r) => r.kind === 'sim')
const probes = results.filter((r) => r.kind === 'probe')
log(`batch done: ${sims.length}/${simItems.length} decisions, ${probes.length}/${probeItems.length} probes`)
return { run_id: manifest.run_id, sims, probes }
