// Workflow for one reservation-summary batch (see docs/orchestration-metadata.md).
//   Workflow({ scriptPath: "scripts/reservations_batch_workflow.js",
//              args: { manifestPath: "<abs path to reservations batch-NNN.json>" } })
// One Haiku agent per request file; each file is self-contained (instructions +
// scrubbed reservation bodies). Agents that die return null and stay pending —
// the next `aidag reservations-prepare` re-issues them. Run-INDEPENDENT and
// single-kind: the manifest carries no run_id and no kind.

export const meta = {
  name: 'aidag-reservations-batch',
  description: 'One checkpointed batch of party-blind reservation (Nej-alternative) summaries',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Summarize', detail: 'one Haiku agent per request file', model: 'haiku' },
  ],
}

const UNITS_SCHEMA = {
  type: 'object',
  properties: {
    units: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          votering_id: { type: 'string' },
          alt_id: { type: 'string' },
          subject: {
            type: 'object',
            properties: { sv: { type: 'string' }, en: { type: 'string' } },
            required: ['sv', 'en'],
            additionalProperties: false,
          },
        },
        required: ['votering_id', 'alt_id', 'subject'],
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
    n_items: { type: 'number' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: { path: { type: 'string' }, n_units: { type: 'number' } },
        required: ['path', 'n_units'],
        additionalProperties: false,
      },
    },
  },
  required: ['n_items', 'items'],
  additionalProperties: false,
}

if (typeof args === 'string') { try { args = JSON.parse(args) } catch {} }
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('reservations')) {
  throw new Error(`reservations_batch_workflow: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}

phase('Load')
const manifest = await agent(
  `Read the file ${args.manifestPath} and return its exact contents as structured output. Do not modify anything.`,
  { label: 'load-manifest', model: 'haiku', schema: MANIFEST_SCHEMA },
)
if (!manifest) throw new Error('manifest loader agent returned null — relaunch the workflow')
if (manifest.items.length !== manifest.n_items) {
  throw new Error(`manifest item count ${manifest.items.length} != n_items ${manifest.n_items}`)
}
for (const item of manifest.items) {
  if (!item.path || !item.path.includes('reservations')) {
    throw new Error(`manifest item has bad path: ${JSON.stringify(item)}`)
  }
}
const nUnits = manifest.items.reduce((s, i) => s + i.n_units, 0)
log(`batch loaded: ${manifest.items.length} request files — ${nUnits} reservation units`)

const prompt = (item) =>
  `You are writing neutral, party-blind one-line summaries of Riksdag counter-proposals ` +
  `(reservations) for a research site.\n\n` +
  `Read the JSON file ${item.path}. Follow its "instructions" field exactly and produce one ` +
  `output unit per unit in its "units" array.\n\n` +
  `Rules:\n` +
  `- Read ONLY that one file. No web search, no other files, no other tools.\n` +
  `- The summary comes ONLY from that unit's "reservation_scrubbed" (topic for context): it must ` +
  `be PARTY-BLIND — name no party, no politician, no document number — and state no outcome.\n` +
  `- Return one unit per input unit, same order, copying votering_id and alt_id unchanged.\n` +
  `- Answer only via the structured output.`

const results = (
  await parallel(
    manifest.items.map((item) => () =>
      agent(prompt(item), {
        label: `reservations:${item.path.split('/').pop()}`,
        phase: 'Summarize',
        model: 'haiku',
        schema: UNITS_SCHEMA,
      }).then((r) => (r ? r.units : null))),
  )
).filter(Boolean)

const reservations = results.flat()
log(`batch done: ${reservations.length}/${nUnits} reservation units`)
return { reservations }
