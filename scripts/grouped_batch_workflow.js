// THE workflow for the full run: one agent decides a GROUP of same-party,
// same-context cases, reading that party's corpus once. The corpus is the
// expensive part (53k-126k tokens under p5), so group size is what makes the
// run affordable — ~19k tokens/decision at group 8, ~8k at month scale.
//
//   Workflow({ scriptPath: "scripts/grouped_batch_workflow.js",
//              args: { manifestPath: "<abs path to batch-NNN.json>",
//                      model: "claude-opus-5", effort: "high" } })
//
// p6 (full-v4) REQUIRES an explicit model — see the guard below. The measured
// operating point is claude-opus-5 / high / chunk 10 / 3-4 citations, at
// ~$0.105 per decision; see docs/grouping-cost-quality-study.md and
// docs/orchestration-full-v4.md. p4/p5 keep their historical defaults so the
// already-published runs stay reproducible.
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
  // p6 narrows to the party's OWN plan — mirrors corpus.DOCS_P6. The narrowing
  // is deliberate (party-blind: no Tidöavtalet, no budget) and is the main
  // methodological difference from the live full-v3 data.
  p6: ['valmanifest', 'partiprogram'],
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

// The loader agent exists for exactly one reason: a workflow script has no
// filesystem access, so an agent is the only way to get a file's contents into
// the script. It must therefore return the SMALLEST projection that the script
// actually uses — never the payload itself.
//
// It used to transcribe every cid. At --batch-size 3000 that is 3,000 cids,
// ~172k chars / ~43k output tokens, against a practical ~24-32k single-response
// ceiling (§4) — i.e. the loader would silently truncate at exactly the batch
// size that makes grouping economical. The cids stay in the manifest FILE
// (`aidag agent-merge` reads them from disk, in Python, to filter decisions);
// the script only ever needed the COUNT, so that is all the agent returns.
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
          // COUNT, not the cid list — see the note above. The group agent reads
          // the actual cases out of `gf` itself; the cids never need to pass
          // through this workflow.
          n: { type: 'number' },
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
// A tier alias ('opus') resolves to whatever the SESSION's Opus is and is not
// stable across sessions — the arms metered as "Opus 4.8" in the grouping study
// were run with 'opus' when that was the session model. Pin an explicit id
// ('claude-opus-5') for anything whose cost or stances must stay comparable.
const MODEL = args.model || 'sonnet'
if (!['sonnet', 'opus', 'haiku'].includes(MODEL) && !MODEL.startsWith('claude-')) {
  throw new Error(`grouped_batch_workflow: unknown model ${JSON.stringify(MODEL)}`)
}
// Thinking depth / token spend. Measured on p6 (docs/grouping-cost-quality-study.md):
// `high` is the operating point — `medium` costs the same and drifts, `xhigh`
// costs 48% more and returned identical decisions. Undefined = session default.
const EFFORT = args.effort
if (EFFORT && !['low', 'medium', 'high', 'xhigh', 'max'].includes(EFFORT)) {
  throw new Error(`grouped_batch_workflow: unknown effort ${JSON.stringify(EFFORT)}`)
}

phase('Load')
const manifest = await agent(
  `Read the JSON file ${args.manifestPath} and return a SUMMARY of it as structured output.\n\n`
  + `Copy these top-level fields exactly as they appear: run_id, prompt_version, n_sims, n_probes, `
  + `cases_dir, groups_dir, probes_dir, system_dir, out_dir.\n\n`
  + `For "items", return one entry per element of the file's items array, in the SAME ORDER:\n`
  + `- for kind "simgroup": {"kind","party","sys","gf","n"} where n is the LENGTH of that item's `
  + `"cids" array (a number — do NOT copy the cids themselves).\n`
  + `- for kind "probe": {"kind","vid"}.\n\n`
  + `Do not omit, reorder or invent items. Do not include the cid strings anywhere in your output.`,
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
  }))
const probeItems = manifest.items
  .filter((i) => i.kind === 'probe')
  .map((i) => ({ vid: i.vid, path: `${manifest.probes_dir}/${i.vid}.json` }))

// verify the manifest survived the loader agent's transcription
const PARTY_SET = new Set(PARTY_CODES)
const problems = []
const totalCases = groups.reduce((s, i) => s + (i.n || 0), 0)
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
  // Cid well-formedness is not re-checked here: the cids are produced
  // deterministically by `aidag agent-prepare` and validated again on the way
  // back by `agent-ingest`, so round-tripping them through an LLM purely to
  // re-validate them would cost more than it protects. What DOES need checking
  // is that the loader transcribed a plausible count for every group — the
  // batch-level `totalCases !== n_sims` assertion above turns any per-group
  // miscount into a hard failure.
  if (!Number.isInteger(item.n) || item.n <= 0) {
    problems.push(`group ${item.party}/${item.gf}: bad case count ${JSON.stringify(item.n)}`)
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
const IS_P6 = promptVersion >= 'p6'
// p6 was A/B'd at 5/10/20 (study §4): 20 cost 13% more for identical quality and
// the model ignored the instruction anyway; 5 added a second cache blowout. 10 is
// the measured optimum. p4/p5 keep 25 so already-published runs stay reproducible.
const CHUNK = IS_P6 ? 10 : 25
const CITABLE = DOCS_BY_VERSION[promptVersion]
// A p6 full run on the wrong model is a ~$2k, 18-hour mistake that only shows up
// at analysis time, so refuse to infer it. p4/p5 keep their historical default.
if (IS_P6 && !args.model) {
  throw new Error('grouped_batch_workflow: p6 requires an explicit model '
    + '(e.g. model: "claude-opus-5", effort: "high") — refusing to fall back to sonnet')
}
log(`batch loaded: ${groups.length} groups (${totalCases} decisions), `
  + `${probeItems.length} probes (run ${manifest.run_id}, prompt ${promptVersion}, `
  + `model ${MODEL}${EFFORT ? `/${EFFORT}` : ''}, chunk ${CHUNK}, citable: ${CITABLE.join('/')})`)

// p6 splits policy stance from parliamentary behaviour: the agent states what the
// party's PLAN implies (`hallning`) and the vote is derived in code. Asking a plan
// to forecast floor tactics is the category error p6 exists to remove, so `rost`
// must not reappear here. Field list mirrors promptgen.DECISION_SCHEMA_P6.
const DECIDING_RULES_P6 =
  `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. Do NOT seek consistency across cases and do NOT let one decision influence another; the same commitment can point one way in one division and the other way in another.\n` +
  `- Base each decision ONLY on the documents in the role file and that case's own text.\n` +
  `- Do NOT use knowledge of how the party actually voted, news, or later events.\n` +
  `- Do NOT weigh in party tactics, coalition loyalty or how other parties vote.\n` +
  `- Uncertainty is valid: use confidence="low" / coverage="not_covered" when the plan does not reach the case. Judge coverage honestly — "explicit" means the plan speaks directly to what this division decides, not merely to the policy area.\n`

// The `many` regime, measured in study §5: it lifted citation density from 2.47 to
// 3.90 per decision at IDENTICAL cost, because the expense is the corpus SEARCH,
// not the citing — the `few` variant cost the same and delivered a third of the
// evidence with no better targeting. Do not thin this without re-measuring.
const CITATION_RULES_P6 = (citable) =>
  `CITATIONS — this is the part that matters most, and the part most often done thinly:\n` +
  `- Give 3 to 4 citations for a typical decision. Drop to 1-2 ONLY when the plan genuinely touches this case in just one or two places; if you find yourself writing a single citation repeatedly, you are under-reading the documents.\n` +
  `- Order them by importance: the FIRST citation must be the commitment that actually carries the vote, not a generic statement of values. Later citations add supporting or qualifying commitments.\n` +
  `- "document" MUST be one of: ${citable.join(', ')}.\n` +
  `- Each "quote" MUST be a verbatim substring of that document in the role file — copy it exactly, do not paraphrase, do not repair grammar, do not join text across a gap. Prefer the specific passage over the general one.\n` +
  `- Give each citation a short "princip" (2-6 Swedish words) naming the commitment it encodes, e.g. "minskad asylinvandring".\n` +
  `- Do not pad: a fourth citation that repeats the third adds nothing. Distinct commitments only.\n`

const FIELDS_P6 =
  `- Each decision is a JSON object with EXACTLY these fields:\n` +
  `    "cid": string — copy it EXACTLY from the case in the group file.\n` +
  `    "hallning": "stodjer" or "avvisar" — the plan's stance on what the MOTFORSLAG demands (NOT a prediction of how the party voted, and NOT "which side won").\n` +
  `    "confidence": one of "high","medium","low".\n` +
  `    "coverage": one of "explicit","inferred","not_covered".\n` +
  `    "motivering": Swedish, 2-4 sentences, stating the decisive plan commitment and how it bears on the motförslag's demand.\n` +
  `    "citations": array of {"document","quote","princip"} — see the citation rules below.\n` +
  `    "omvarld": {"paverkar": boolean, "faktorer": array of {"faktor","effekt"}}. Weigh the case's worldstate block in ONLY when it materially changes how the documents apply (then paverkar=true, max 3 faktorer; otherwise paverkar=false and faktorer=[]).\n` +
  `    "flags": array of strings (usually []).\n` +
  `    "plan_tacker_utskottets_skal": "ja" or "nej" — does the plan speak to the UTSKOTT's stated reason? Answer this independently of hallning.\n`

const FIELDS_LEGACY = (citable) =>
  `- Each decision is a JSON object with EXACTLY these fields:\n` +
  `    "cid": string — copy it EXACTLY from the case in the group file.\n` +
  `    "rost": one of "Ja","Nej","Avstår".\n` +
  `    "confidence": one of "high","medium","low".\n` +
  `    "coverage": one of "explicit","inferred","not_covered".\n` +
  `    "motivering": Swedish, SHORT — 2-4 sentences (max ~80 words) stating the decisive plan commitment and how it maps to Ja/Nej/Avstår.\n` +
  `    "citations": array of {"document","quote","princip"}. "document" MUST be one of: ${citable.join(', ')}. ` +
  `"quote" must be a VERBATIM substring copied exactly from that document's text — pick the SPECIFIC passage that carries the vote, not a generic statement. ` +
  `Order by importance (the FIRST citation is decisive). "princip" is 2-6 Swedish words naming the commitment.\n` +
  `    "omvarld": {"paverkar": boolean, "faktorer": array of {"faktor","effekt"}}. Weigh the case's worldstate block in ONLY when it materially changes how the documents apply (then paverkar=true, max 3 faktorer; otherwise paverkar=false and faktorer=[]). If the documents are silent and worldstate decides, use coverage="not_covered" AND paverkar=true.\n` +
  `    "flags": array of strings (usually []).\n`

const groupPrompt = (item) =>
  `You decide how a Swedish party SHOULD vote in ${item.n} SEPARATE Riksdag divisions, strictly from the party's own documents, and WRITE each decision to a file.\n\n` +
  `1. Read the file ${item.system_file} — it is your complete role and instructions, including the party's documents. Read it ONCE; it applies to every case.\n` +
  `2. Read the file ${item.group_file}. Its "cases" array holds all ${item.n} cases: each entry has a "cid" and a "user" field with that case's text.\n\n` +
  `Deciding — rules:\n` +
  (IS_P6 ? DECIDING_RULES_P6 :
    `- Decide EVERY case INDEPENDENTLY on its own merits, as if it were the only case in front of you. ` +
    `Do NOT seek consistency across the cases in this batch and do NOT let one decision influence another; ` +
    `the same commitment can point to Ja in one division and Nej in another depending on what is actually being voted on.\n` +
    `- Base each decision exclusively on the documents in the role file and the situational picture in that case's text. Do NOT use your own knowledge of how the party actually voted or of events after the stated time.\n`) +
  (IS_P6 ? FIELDS_P6 : FIELDS_LEGACY(CITABLE)) +
  (IS_P6 ? `\n${CITATION_RULES_P6(CITABLE)}` : '') +
  `\n` +
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
      label: `${item.party}:${item.n} cases`,
      phase: 'Simulate',
      model: MODEL,
      ...(EFFORT ? { effort: EFFORT } : {}),
      agentType: 'general-purpose', // needs the Write tool
      schema: GROUP_RECEIPT_SCHEMA,
    }).then((r) => (r ? { kind: 'group', party: item.party, gf: item.gf, written: r.written ?? 0 } : { kind: 'group', party: item.party, gf: item.gf, written: 0, error: true }))),
  ...probeItems.map((item) => () =>
    agent(probePrompt(item), { label: `probe:${item.vid}`, phase: 'Probe', model: MODEL, ...(EFFORT ? { effort: EFFORT } : {}), schema: PROBE_SCHEMA })
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
