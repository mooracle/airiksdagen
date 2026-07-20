// Workflow script for one case-metadata batch (see docs/orchestration-metadata.md).
// Invoke from the orchestrator session with:
//   Workflow({ scriptPath: "scripts/metadata_batch_workflow.js",
//              args: { manifestPath: "<abs path to metadata batch-NNN.json>" } })
// One Haiku agent per request file; each file is self-contained (instructions +
// two grounding blocks per case). Agents that die return null and stay pending —
// the next `aidag metadata-prepare` re-issues exactly the gaps.
//
// Run-INDEPENDENT and single-kind: unlike translate, the manifest carries no
// `run_id` and no `kind` split.

export const meta = {
  name: 'aidag-metadata-batch',
  description: 'One checkpointed batch of case metadata (subject/at_stake/subtopics + de-leaked agent view)',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Metadata', detail: 'one Haiku agent per request file', model: 'haiku' },
  ],
}

const METADATA_UNITS_SCHEMA = {
  type: 'object',
  properties: {
    units: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          votering_id: { type: 'string' },
          subject: {
            type: 'object',
            properties: { sv: { type: 'string' }, en: { type: 'string' } },
            required: ['sv', 'en'],
            additionalProperties: false,
          },
          at_stake: {
            type: 'object',
            properties: { sv: { type: 'string' }, en: { type: 'string' } },
            required: ['sv', 'en'],
            additionalProperties: false,
          },
          subtopics: { type: 'array', items: { type: 'string' } },
          agent: {
            type: 'object',
            properties: { subject: { type: 'string' }, at_stake: { type: 'string' } },
            required: ['subject', 'at_stake'],
            additionalProperties: false,
          },
        },
        required: ['votering_id', 'subject', 'at_stake', 'subtopics', 'agent'],
        additionalProperties: false,
      },
    },
  },
  required: ['units'],
  additionalProperties: false,
}

// Metadata manifest DROPS run_id and the kind enum (run-independent, single-kind).
const MANIFEST_SCHEMA = {
  type: 'object',
  properties: {
    n_items: { type: 'number' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          path: { type: 'string' },
          n_units: { type: 'number' },
        },
        required: ['path', 'n_units'],
        additionalProperties: false,
      },
    },
  },
  required: ['n_items', 'items'],
  additionalProperties: false,
}

// Fail fast if args were not forwarded — never let the loader agent guess.
// Tolerate args arriving as a JSON-encoded string (a recurring launch mistake).
if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch { /* falls through to the guard below */ }
}
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('metadata')) {
  throw new Error(`metadata_batch_workflow: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}

phase('Load')
const manifest = await agent(
  `Read the file ${args.manifestPath} and return its exact contents as structured output. Do not modify anything.`,
  { label: 'load-manifest', model: 'haiku', schema: MANIFEST_SCHEMA },
)
if (!manifest) {
  throw new Error('manifest loader agent returned null (agent error) — nothing was run; relaunch the workflow')
}
// verify the manifest survived the loader agent's transcription
if (manifest.items.length !== manifest.n_items) {
  throw new Error(`manifest item count ${manifest.items.length} != n_items ${manifest.n_items}`)
}
for (const item of manifest.items) {
  if (!item.path || !item.path.includes('metadata')) {
    throw new Error(`manifest item has bad path: ${JSON.stringify(item)}`)
  }
}
const nUnits = manifest.items.reduce((s, i) => s + i.n_units, 0)
log(`batch loaded: ${manifest.items.length} request files — ${nUnits} case units`)

const prompt = (item) =>
  `You are writing neutral, factual metadata for a public research site about Sweden's Riksdag votes.\n\n` +
  `Read the JSON file ${item.path}. Follow its "instructions" field exactly and produce one output ` +
  `unit for every unit in its "units" array.\n\n` +
  `Rules:\n` +
  `- Read ONLY that one file. No web search, no other files, no other tools.\n` +
  `- Return one output unit per input unit, in the SAME order, copying votering_id unchanged.\n` +
  `- Human fields (subject, subtopics, at_stake) come from that unit's "display_src".\n` +
  `- The "agent" object comes ONLY from that unit's "agent_src" (the scrubbed input): it must be ` +
  `party-blind and pre-decision — name no party, no politician, no document number, no date, and ` +
  `state no outcome.\n` +
  `- Ground every field strictly in the unit's own text; invent no facts.\n` +
  `- Answer only via the structured output.`

const results = (
  await parallel(
    manifest.items.map((item) => () =>
      agent(prompt(item), {
        label: `metadata:${item.path.split('/').pop()}`,
        phase: 'Metadata',
        model: 'haiku',
        schema: METADATA_UNITS_SCHEMA,
      }).then((r) => (r ? r.units : null))),
  )
).filter(Boolean)

const cases = results.flat()
log(`batch done: ${cases.length}/${nUnits} case units`)
return { cases }
