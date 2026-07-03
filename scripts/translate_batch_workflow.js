// Workflow script for one translation batch (see docs/orchestration-full-run.md).
// Invoke from the orchestrator session with:
//   Workflow({ scriptPath: "scripts/translate_batch_workflow.js",
//              args: { manifestPath: "<abs path to translate batch-NNN.json>" } })
// One Sonnet agent per request file; each file is self-contained (instructions
// + Swedish source units). Agents that die return null and stay pending — the
// next `aidag translate-prepare` re-issues them.

export const meta = {
  name: 'aidag-translate-batch',
  description: 'One checkpointed batch of English translations (case texts + AI decisions)',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Translate', detail: 'one Sonnet agent per request file', model: 'sonnet' },
  ],
}

const CASE_UNITS_SCHEMA = {
  type: 'object',
  properties: {
    units: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          votering_id: { type: 'string' },
          rubrik: { type: 'string' },
          dok_titel: { type: 'string' },
          forslag_text: { type: 'string' },
          notis: { type: 'string' },
          alternatives: { type: 'array', items: { type: 'string' } },
        },
        required: ['votering_id', 'rubrik', 'dok_titel', 'forslag_text', 'notis', 'alternatives'],
        additionalProperties: false,
      },
    },
  },
  required: ['units'],
  additionalProperties: false,
}

const DECISION_UNITS_SCHEMA = {
  type: 'object',
  properties: {
    units: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          cid: { type: 'string' },
          motivering: { type: 'string' },
          citations: {
            type: 'array',
            items: {
              type: 'object',
              properties: { quote: { type: 'string' }, princip: { type: 'string' } },
              required: ['quote', 'princip'],
              additionalProperties: false,
            },
          },
          omvarld: {
            type: 'array',
            items: {
              type: 'object',
              properties: { faktor: { type: 'string' }, effekt: { type: 'string' } },
              required: ['faktor', 'effekt'],
              additionalProperties: false,
            },
          },
        },
        required: ['cid', 'motivering', 'citations', 'omvarld'],
        additionalProperties: false,
      },
    },
  },
  required: ['units'],
  additionalProperties: false,
}

const MANIFEST_SCHEMA = {
  type: 'object',
  properties: {
    run_id: { type: 'string' },
    n_items: { type: 'number' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          kind: { type: 'string', enum: ['cases', 'decisions'] },
          path: { type: 'string' },
          n_units: { type: 'number' },
        },
        required: ['kind', 'path', 'n_units'],
        additionalProperties: false,
      },
    },
  },
  required: ['run_id', 'n_items', 'items'],
  additionalProperties: false,
}

// Fail fast if args were not forwarded — never let the loader agent guess.
// Tolerate args arriving as a JSON-encoded string (a recurring launch mistake).
if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch { /* falls through to the guard below */ }
}
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('translate')) {
  throw new Error(`translate_batch_workflow: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}

phase('Load')
const manifest = await agent(
  `Read the file ${args.manifestPath} and return its exact contents as structured output. Do not modify anything.`,
  { label: 'load-manifest', model: 'sonnet', schema: MANIFEST_SCHEMA },
)
if (!manifest) {
  throw new Error('manifest loader agent returned null (agent error) — nothing was run; relaunch the workflow')
}
// verify the manifest survived the loader agent's transcription
if (manifest.items.length !== manifest.n_items) {
  throw new Error(`manifest item count ${manifest.items.length} != n_items ${manifest.n_items}`)
}
for (const item of manifest.items) {
  if (!item.path || !item.path.includes('translate')) {
    throw new Error(`manifest item has bad path: ${JSON.stringify(item)}`)
  }
}
const nCases = manifest.items.filter((i) => i.kind === 'cases').reduce((s, i) => s + i.n_units, 0)
const nDecisions = manifest.items.filter((i) => i.kind === 'decisions').reduce((s, i) => s + i.n_units, 0)
log(`batch loaded: ${manifest.items.length} request files — ${nCases} case units, ${nDecisions} decision units (run ${manifest.run_id})`)

const prompt = (item) =>
  `You are a professional Swedish→English translator for a research site about Riksdag votes.\n\n` +
  `Read the JSON file ${item.path}. Follow its "instructions" field exactly and translate every ` +
  `unit in its "units" array to English.\n\n` +
  `Rules:\n` +
  `- Read ONLY that one file. No web search, no other files, no other tools.\n` +
  `- Return one output unit per input unit, in the SAME order, copying the id field ` +
  `(votering_id or cid) unchanged.\n` +
  `- Every array in a unit (alternatives, citations, omvarld) must keep exactly the same ` +
  `length and order as the input.\n` +
  `- Empty input strings stay empty. Translate everything else fully — no summarizing.\n` +
  `- Answer only via the structured output.`

const results = (
  await parallel(
    manifest.items.map((item) => () =>
      agent(prompt(item), {
        label: `${item.kind}:${item.path.split('/').pop()}`,
        phase: 'Translate',
        model: 'sonnet',
        schema: item.kind === 'cases' ? CASE_UNITS_SCHEMA : DECISION_UNITS_SCHEMA,
      }).then((r) => (r ? { kind: item.kind, units: r.units } : null))),
  )
).filter(Boolean)

const cases = results.filter((r) => r.kind === 'cases').flatMap((r) => r.units)
const decisions = results.filter((r) => r.kind === 'decisions').flatMap((r) => r.units)
log(`batch done: ${cases.length}/${nCases} case units, ${decisions.length}/${nDecisions} decision units`)
return { run_id: manifest.run_id, cases, decisions }
