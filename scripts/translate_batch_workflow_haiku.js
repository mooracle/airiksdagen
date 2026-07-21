// Haiku translation workflow — one agent per request file, each agent WRITES its
// own result file so the bulk translation text never round-trips through the
// orchestrator's context (the 2,539-case full re-translation is too large for
// the return-value flow of translate_batch_workflow.js).
//
// Invoke:
//   Workflow({ scriptPath: "scripts/translate_batch_workflow_haiku.js",
//              args: { manifestPath: "<abs path to translate batch-NNN.json>" } })
//
// Each agent reads reqs/<name>.json, translates, and writes out/<name>.json as
// {"kind":"cases","units":[...]}. The orchestrator then merges out/*.json into a
// single {cases,decisions} file and runs `aidag translate-ingest` (which validates
// alignment and is idempotent — malformed or missing files simply stay pending for
// the next prepare→run cycle).

export const meta = {
  name: 'aidag-translate-batch-haiku',
  description: 'Checkpointed English case-text translations with Haiku; each agent writes its own result file',
  phases: [
    { title: 'Load', detail: 'read the batch manifest' },
    { title: 'Translate', detail: 'one Haiku agent per request file → writes out/<name>.json', model: 'haiku' },
  ],
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

const WROTE_SCHEMA = {
  type: 'object',
  properties: {
    out_path: { type: 'string' },
    n_units: { type: 'number' },
  },
  required: ['out_path', 'n_units'],
  additionalProperties: false,
}

if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch { /* falls through to the guard below */ }
}
if (!args || typeof args.manifestPath !== 'string' || !args.manifestPath.includes('translate')) {
  throw new Error(`translate_batch_workflow_haiku: args.manifestPath missing or invalid: ${JSON.stringify(args)}`)
}

phase('Load')
const manifest = await agent(
  `Read the file ${args.manifestPath} and return its exact contents as structured output. Do not modify anything.`,
  { label: 'load-manifest', model: 'haiku', schema: MANIFEST_SCHEMA },
)
if (!manifest) {
  throw new Error('manifest loader agent returned null (agent error) — nothing was run; relaunch the workflow')
}
if (manifest.items.length !== manifest.n_items) {
  throw new Error(`manifest item count ${manifest.items.length} != n_items ${manifest.n_items}`)
}
const outPathFor = (reqPath) => reqPath.replace('/reqs/', '/out/')
log(`batch loaded: ${manifest.items.length} request files (run ${manifest.run_id})`)

const prompt = (item) => {
  const outPath = outPathFor(item.path)
  return (
    `You are a professional Swedish→English translator for a research site about Riksdag votes.\n\n` +
    `1. Read the JSON file ${item.path}. It has an "instructions" field and a "units" array.\n` +
    `2. Follow its "instructions" exactly and translate every unit to English.\n` +
    `3. Write the result to ${outPath} as a JSON object with this exact shape and NOTHING else:\n` +
    `   {"kind":"${item.kind}","units":[ ...one translated unit per input unit... ]}\n\n` +
    `Rules:\n` +
    `- Read ONLY that one input file and write ONLY that one output file. No web, no other tools.\n` +
    `- One output unit per input unit, SAME order. Copy the id field (votering_id or cid) unchanged.\n` +
    `- For case units, output keys: votering_id, rubrik, dok_titel, forslag_text, notis, alternatives.\n` +
    `- Every array (alternatives, citations, omvarld) keeps the SAME length and order as the input.\n` +
    `- Empty input strings stay empty ("") ; translate everything else fully, no summarizing.\n` +
    `- The file MUST be valid JSON (double quotes, no trailing commas, no markdown fence, no prose).\n` +
    `- After writing, return the out_path and the number of units you wrote via structured output.`
  )
}

const wrote = (
  await parallel(
    manifest.items.map((item) => () =>
      agent(prompt(item), {
        label: `${item.kind}:${item.path.split('/').pop()}`,
        phase: 'Translate',
        model: 'haiku',
        schema: WROTE_SCHEMA,
      }).then((r) => (r ? { path: item.path, out_path: r.out_path, n_units: r.n_units } : null))),
  )
).filter(Boolean)

const wroteUnits = wrote.reduce((s, w) => s + (w.n_units || 0), 0)
log(`batch done: ${wrote.length}/${manifest.items.length} files written, ${wroteUnits} units`)
return { run_id: manifest.run_id, files_written: wrote.length, units: wroteUnits, out: wrote.map((w) => w.out_path) }
