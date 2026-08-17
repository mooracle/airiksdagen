// Blind stance recheck: does the published Ja/Nej follow from the citations?
//
// Invoke from the orchestrator session with the line `stance-recheck-prepare` prints:
//   Workflow({ scriptPath: "scripts/stance_recheck_workflow.js",
//              args: { reqDir, outDir, prefix, count, runId, model: "sonnet" } })
//
// Lives in scripts/ rather than .claude/workflows/ because .claude/ is gitignored,
// and a workflow the repo cannot reproduce after a clone is not part of the pipeline.
//
// One judge agent per request file. Each file is self-contained: the instructions,
// then per case the <arende> the deciding agent was served plus one unit per party
// carrying only that decision's citations and motivering — never its `hallning` or
// `rost`. That blindness is what makes the comparison mean anything; if a future
// edit puts the stored stance into a unit, the pass stops measuring anything and
// starts agreeing with itself.
//
// Request filenames are derived from prefix + count rather than read out of the
// manifest, so there is no loader agent to mis-transcribe a path (the manifest is
// the record; `prepare` prints count). Agents that die return null and their units
// stay pending — the next `stance-recheck-prepare` re-issues exactly the gaps. Do
// not trust this workflow's own completion count; re-run `stance-recheck-status`.

export const meta = {
  name: 'stance-recheck',
  description: 'Blind re-derivation of each p6 stance from its own citations, to catch inverted votes',
  phases: [{ title: 'Judge', detail: 'one agent per request file, ~8 cases each' }],
}

const a = typeof args === 'string' ? JSON.parse(args) : (args || {})
const { reqDir, outDir, prefix, count, runId } = a
if (!reqDir || !outDir || !prefix || !count) {
  throw new Error(`stance-recheck: args must carry reqDir, outDir, prefix, count — got ${JSON.stringify(a)}`)
}

// Sonnet by default. The task is textual entailment over a bounded amount of
// supplied text, not open-ended reasoning, and the judgement has to be consistent
// across hundreds of independent agents — the instructions pin the definitions the
// way translate's glossary does. Pass model:'opus' to re-audit the disagreements
// this pass surfaces, where the cost is worth it on a much smaller set.
const MODEL = a.model || 'sonnet'
if (!['haiku', 'sonnet', 'opus'].includes(MODEL)) {
  throw new Error(`stance-recheck: model must be haiku|sonnet|opus, got ${JSON.stringify(MODEL)}`)
}
const EFFORT = a.effort

const files = Array.from({ length: count }, (_, i) => `${prefix}${String(i).padStart(4, '0')}.json`)
log(`recheck ${runId || ''}: ${files.length} judge agents, model=${MODEL}`)

phase('Judge')

const PROMPT = (f) => `Audit whether each actor's stance follows from the passages it cites.

1. Read the JSON file at this exact path: ${reqDir}/${f}
   Shape {instructions, cases:[{votering_id, arende, units:[{cid, citations, motivering}]}]}.
2. Follow "instructions" EXACTLY. For each case, the "arende" block IS the vote: the committee's
   position, and the counter-proposal under "Motforslag i voteringen". For every unit under that
   case, decide from that unit's own citations and motivering ALONE.
   You are NOT told what the actor concluded. Do not try to identify the party, and do not use any
   knowledge of how anyone actually voted — judge only the text in front of you.
3. Write ONE JSON file to this exact path: ${outDir}/${f}
   Shape: {"units":[<one verdict per input unit>]}. Copy every cid verbatim.
   Produce exactly one verdict per input unit, across every case in the file.
4. Reply with ONLY the number of verdicts you wrote.`

const results = await parallel(
  files.map((f) => () =>
    agent(PROMPT(f), {
      label: `judge:${f.replace(prefix, '').replace('.json', '')}`,
      phase: 'Judge',
      model: MODEL,
      ...(EFFORT ? { effort: EFFORT } : {}),
      agentType: 'general-purpose',
    })
      .then(() => ({ f, ok: true }))
      .catch((e) => ({ f, ok: false, err: String((e && e.message) || e) }))
  )
)

const ok = results.filter((r) => r && r.ok).length
log(`${ok}/${files.length} agents wrote output -> ${outDir}`)
return {
  agents: files.length,
  ok,
  failed: results.filter((r) => !r || !r.ok).map((r) => (r ? r.f : 'null')),
  outDir,
  next: `uv run aidag stance-recheck-ingest --run-id ${runId} --input ${outDir} --model <model>`,
}
