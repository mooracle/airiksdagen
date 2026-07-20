// THE workflow for the full run: one agent decides a GROUP of same-party,
// same-context cases, reading that party's corpus once. The corpus is the
// expensive part (53k-126k tokens under p5), so group size is what makes the
// run affordable — ~19k tokens/decision at group 8, ~8k at month scale.
//
//   Workflow({ scriptPath: "scripts/grouped_batch_workflow.js",
//              args: { manifestPath: "<abs path to batch-NNN.json>",
//                      model: "opus" } })     // model optional, default sonnet
//
// Manifest items must be kind 'simgroup' (aidag agent-prepare --group N, or
// --group 0 for size-driven packing). The citable-document set comes from the
// manifest's prompt_version.
//
// Each group agent WRITES its decisions as JSONL to manifest.out_dir (in chunks,
// so no single response hits the output-token ceiling — this is what lets a group
// hold 100+ cases). This workflow RETURNS only receipts + probes; the decisions
// are on disk. After it finishes, merge and ingest:
//   aidag agent-merge  --run-id <run>            # out files -> {sims, probes} json
//   aidag agent-ingest --run-id <run> --input <merged.json> ...

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

// The per-decision shape agents write to JSONL is documented in the prompt (not
// enforced by a return schema, since decisions no longer come back through the
// response). Pydantic validation on `agent-ingest` is the gate; a malformed
// decision is skipped there and its cid re-issues on the next prepare.
// The group agent's RESPONSE is just a small receipt: how many it wrote and
// which cids — used only to log coverage and cross-check against the files.
const GROUP_RECEIPT_SCHEMA = {
  type: 'object',
  properties: {
    written: { type: 'number' },
    cids: { type: 'array', items: { type: 'string' } },
  },
  required: ['written', 'cids'],
  additionalProperties: false,
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
    groups_dir: { type: 'string' },
    probes_dir: { type: 'string' },
    system_dir: { type: 'string' },
    out_dir: { type: 'string' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          kind: { type: 'string', enum: ['simgroup', 'probe'] },
          party: { type: 'string' },
          sys: { type: 'string' },
          gf: { type: 'string' },
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
    'cases_dir', 'groups_dir', 'probes_dir', 'system_dir', 'out_dir', 'items',
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
    group_file: `${manifest.groups_dir}/${item.gf}`,
    // where this agent writes its decisions — one or more JSONL files
    // g-NNNN-001.jsonl, g-NNNN-002.jsonl, … under the batch's out dir
    out_stem: `${manifest.out_dir}/${item.gf.replace(/\.json$/, '')}`,
    cases: item.cids.map((cid) => ({ cid, vid: cid.split(':')[1] ?? '' })),
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
for (const dir of [manifest.cases_dir, manifest.groups_dir, manifest.probes_dir, manifest.system_dir, manifest.out_dir]) {
  if (!dir || !dir.includes('agentrun')) problems.push(`bad manifest dir: ${dir}`)
}
for (const item of groups) {
  if (!item.sys || !item.gf || !PARTY_SET.has(item.party)) {
    problems.push(`bad group item: ${item.party} / sys=${item.sys} / gf=${item.gf}`)
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
// Chunk size for incremental writes. Each block of up to CHUNK cases is written
// as one JSONL file, so no single Write is large enough to be truncated — that is
// exactly what lets a group hold 100+ cases (a single 100-decision response would
// blow the ~24-32k output ceiling; many small writes across turns never do).
const CHUNK = 25
const CITABLE = DOCS_BY_VERSION[promptVersion]
log(`batch loaded: ${groups.length} groups (${totalCases} decisions), ${probeItems.length} probes (run ${manifest.run_id}, prompt ${promptVersion}, model ${MODEL}, citable: ${CITABLE.join('/')})`)

const groupPrompt = (item) =>
  `You decide how a Swedish party SHOULD vote in ${item.cases.length} SEPARATE Riksdag divisions, strictly from the party's own documents, and WRITE each decision to a file.\n\n` +
  `1. Read the file ${item.system_file} — it is your complete role and instructions, including the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read the file ${item.group_file}. Its "cases" array holds all ${item.cases.length} cases: each entry has a "cid" and a "user" field with that case's text.\n\n` +
  `Deciding — rules:\n` +
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. ` +
  `Do NOT seek consistency across the cases in this batch and do NOT let one decision influence another; ` +
  `the same commitment can point to Ja in one division and Nej in another depending on what is actually being voted on.\n` +
  `- Base each decision exclusively on the documents in the role file and the situational picture in that case's text. Do NOT use your own knowledge of how the party actually voted or of events after the stated time.\n` +
  `- Each decision is a JSON object with EXACTLY these fields:\n` +
  `    "cid": string — copy it EXACTLY from the case in the group file.\n` +
  `    "rost": one of "Ja","Nej","Avstår".\n` +
  `    "confidence": one of "high","medium","low".\n` +
  `    "coverage": one of "explicit","inferred","not_covered".\n` +
  `    "motivering": Swedish, SHORT — 2-4 sentences (max ~80 words) stating the decisive plan commitment and how it maps to Ja/Nej/Avstår.\n` +
  `    "citations": array of {"document","quote","princip"}. "document" MUST be one of: ${CITABLE.join(', ')}. ` +
  `"quote" must be a VERBATIM substring copied exactly from that document's text — pick the SPECIFIC passage that carries the vote, not a generic statement. ` +
  `Order by importance (the FIRST citation is decisive). "princip" is 2-6 Swedish words naming the commitment.\n` +
  `    "omvarld": {"paverkar": boolean, "faktorer": array of {"faktor","effekt"}}. Weigh the case's worldstate block in ONLY when it materially changes how the documents apply (then paverkar=true, max 3 faktorer; otherwise paverkar=false and faktorer=[]). If the documents are silent and worldstate decides, use coverage="not_covered" AND paverkar=true.\n` +
  `    "flags": array of strings (usually []).\n\n` +
  `Writing — rules:\n` +
  `- Write the decisions using the Write tool to files named ${item.out_stem}-001.jsonl, ${item.out_stem}-002.jsonl, and so on.\n` +
  `- Work through the cases in blocks of at most ${CHUNK}. After you finish a block, Write it to the next numbered file BEFORE moving on — do NOT hold all decisions until the end.\n` +
  `- Each file's content is JSONL: one compact single-line JSON object per case, one per line. No surrounding array brackets, no markdown code fences, no commentary — just the JSON objects, one per line.\n` +
  `- Read ONLY the two input files above and Write ONLY under ${manifest.out_dir}. No web search, no other files.\n` +
  `- When every case has been written, respond via the structured output with {"written": <total decisions written>, "cids": [<every cid you wrote>]}. Do not put the decisions themselves in the response.`

const probePrompt = (item) =>
  `Read the JSON file at ${item.path}. Its "prompt" field contains instructions and a question ` +
  `about a real Riksdag vote. Follow them exactly: answer only from your actual memory of this ` +
  `specific vote; if you do not remember it, set recalls_case=false and "okänt" for parties you ` +
  `do not remember. Read only that one file, use no other tools, answer only via structured output.`

const work = [
  ...groups.map((item) => () =>
    agent(groupPrompt(item), {
      label: `${item.party}:${item.cases.length} cases`,
      phase: 'Simulate',
      model: MODEL,
      agentType: 'general-purpose', // needs the Write tool
      schema: GROUP_RECEIPT_SCHEMA,
    }).then((r) => (r ? { kind: 'group', party: item.party, gf: item.gf, written: r.written ?? 0 } : { kind: 'group', party: item.party, gf: item.gf, written: 0, error: true }))),
  ...probeItems.map((item) => () =>
    agent(probePrompt(item), { label: `probe:${item.vid}`, phase: 'Probe', model: MODEL, schema: PROBE_SCHEMA })
      .then((r) => (r ? { kind: 'probe', vid: item.vid, result: r } : null))),
]
const results = (await parallel(work)).filter(Boolean)

// Decisions live in the JSONL files the agents wrote, NOT in this return value —
// `aidag agent-merge` reads manifest.out_dir back into the {sims, probes} payload
// that agent-ingest consumes. Here we only report receipts and pass probes through.
const groupReceipts = results.filter((r) => r.kind === 'group')
const claimed = groupReceipts.reduce((s, r) => s + (r.written || 0), 0)
const failed = groupReceipts.filter((r) => r.error).length
const probes = results.filter((r) => r.kind === 'probe').map(({ vid, result }) => ({ kind: 'probe', vid, result }))
if (failed > 0) log(`WARNING: ${failed}/${groups.length} group agents errored (their cids stay pending, re-issue next prepare)`)
log(`batch done: agents wrote ~${claimed}/${totalCases} decisions to ${manifest.out_dir}; ${probes.length}/${probeItems.length} probes. Run: aidag agent-merge --run-id ${manifest.run_id}`)
return { run_id: manifest.run_id, out_dir: manifest.out_dir, groups: groupReceipts, probes }
