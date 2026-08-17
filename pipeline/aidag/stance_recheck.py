"""Does the published vote actually follow from the citations?

`verify simulate` proves each citation is a real, in-context substring of what the
agent was served. It does not check the step AFTER that: whether the stance — and
so the Ja/Nej the site prints — follows from the passages the decision cites. That
step is where the FöU14 punkt 2 defect lived. Both M and S cited pro-Nato
commitments, wrote in prose that the plan speaks FOR the proposal, returned
`hallning="stodjer"`, and shipped as Nej against a real Ja.

Why this needs a model. The direction of a stance in Swedish prose cannot be read
off the surface: negation and embedding invert it freely, and "Att avslå förslaget
skulle bromsa det" argues AGAINST rejection while containing every token of a
rejection. A regex over the concluding sentence was measured on full-v4 at 30 hits
of which 2 were real — ~7% precision. So the recheck is an independent BLIND
re-derivation: a judge sees the same <arende> the agent saw plus the decision's own
citations and motivering, never its `hallning` or `rost`, and derives the stance
itself. Showing it the stored label would anchor it and the comparison would mean
nothing.

Three findings come out, and they are separate defects:

  sign          the judge's stance disagrees with the stored one — the vote does
                not follow the cited evidence
  contradiction the motivering's own conclusion disagrees with the stored stance —
                the decision contradicts itself on the page, which is what a
                reader sees. This is the FöU14 class.
  ungrounded    the cited passages do not bear on what the counter-proposal
                demands — a stance reasoned from off-axis material

Grouped by CASE, not by decision: the <arende> is the bulk of the input and all
eight parties on a votering share it byte for byte, so bundling a case's decisions
into one request pays it once instead of eight times.

Two limits worth stating plainly, because both bound what a clean report means.

The judge is not party-blind and cannot be: the citations are verbatim party
programme text and routinely name the party. Withholding the party code would buy
nothing while pretending otherwise. What IS withheld is the stored stance, the
derived vote, and the real outcome — which is what the comparison needs.

And one judge reads both the citations and the motivering, so the `hallning` field
is firewalled by instruction and field ORDER rather than by construction: it is
emitted before `motivering_direction`, so the citations-only judgement is committed
before the model has had to state what the actor's prose concludes. That is weaker
than withholding the motivering outright. It is the deliberate trade — a strict
citations-only pass cannot report the self-contradiction finding at all, and that
finding is the one that catches the FöU14 class.

Checkpoint-aware exactly like translate: done means "cid present in
`data/results/recheck/<run>/stances.jsonl`". A dead agent's units stay pending and
the next prepare re-issues precisely those.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from aidag.config import INTERIM_DIR, PROCESSED_DIR, PROMPT_VERSION, RESULTS_DIR, version_ge

# One agent handles this many CASES (~8 decisions each). At 8 the input is roughly
# 8 <arende> blocks plus 64 citation/motivering units — ~40k tokens, comfortably
# inside one context with room for the reasoning.
CASES_PER_REQUEST = 8

# Field ORDER is load-bearing, not cosmetic. dict order is what the model emits in,
# so `citations_bear` and `hallning` are decided before `motivering_direction` —
# the citations-only judgement is committed before the model has had to state what
# the actor's own prose concludes. Reversing these turns the pass into an echo of
# the motivering and it stops finding anything the prose does not already admit.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cid": {"type": "string"},
                    # Whether the cited passages bear on the demand at all.
                    "citations_bear": {"type": "string", "enum": ["yes", "weak", "no"]},
                    # The re-derivation, from the <arende> and the citations ONLY.
                    "hallning": {"type": "string", "enum": ["stodjer", "avvisar"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    # What the decision's OWN prose concludes. Separate from the
                    # above because a decision can cite sound material and still
                    # write a conclusion that contradicts its stored label — which
                    # is exactly the FöU14 punkt 2 failure.
                    "motivering_direction": {
                        "type": "string",
                        "enum": ["stodjer", "avvisar", "unclear"],
                    },
                    "note": {"type": "string"},
                },
                "required": [
                    "cid",
                    "citations_bear",
                    "hallning",
                    "confidence",
                    "motivering_direction",
                    "note",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["units"],
    "additionalProperties": False,
}

INSTRUCTIONS = (
    "You are auditing whether a stance follows from the passages it cites. For EACH unit you get "
    "the case (<arende>: the committee's position, and the counter-proposal under 'Motförslag i "
    "voteringen') and one actor's `citations` — verbatim passages from a party's own programme "
    "and election manifesto, each with a short `princip` — plus its `motivering`, the actor's own "
    "written reasoning. You are NOT told what the actor concluded.\n"
    "The citations are quoted from party documents and often name the party; that is unavoidable "
    "and you may read it. What you must NOT do is use any knowledge of how that party actually "
    "voted, of the real outcome, or of anything outside the supplied text. Judge the text.\n"
    "Answer the fields IN ORDER, and respect what each one is allowed to look at:\n"
    "- citations_bear: do the cited passages actually speak to what this vote turns on? 'yes' if "
    "they address the demand, 'weak' if only a general principle reaches it, 'no' if they are "
    "about something else.\n"
    "- hallning: from the <arende> and the `citations` ONLY — IGNORE the motivering entirely for "
    "this field — does the cited material support ('stodjer') or oppose ('avvisar') what the "
    "counter-proposal DEMANDS in substance? The first citation is the decisive one. Judge the "
    "demand, not which side is which: the committee's reason is often procedural ('an inquiry is "
    "under way') while the counter-proposal is substantive. If the cited material points toward "
    "adopting the committee's proposal, that is 'avvisar'.\n"
    "- confidence: your own certainty in `hallning`.\n"
    "- motivering_direction: NOW read the `motivering`. What does it ITSELF conclude — that the "
    "plan supports the counter-proposal's demand ('stodjer'), opposes it ('avvisar'), or does it "
    "not say ('unclear')? Read the whole argument, not the last sentence: Swedish negation and "
    "embedded clauses invert direction freely, and 'Att avslå förslaget skulle bromsa arbetet' "
    "argues AGAINST rejection while containing every word of one. If the motivering concludes the "
    "party should vote yes to the committee's proposal, that is 'avvisar'.\n"
    "- note: at most 25 words, in English, naming what decided it.\n"
    "Copy each `cid` verbatim. Return exactly one unit per input unit."
)


def run_dir(run_id: str) -> Path:
    return INTERIM_DIR / "recheck" / run_id


def stances_path(run_id: str) -> Path:
    return RESULTS_DIR / "recheck" / run_id / "stances.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def load_stances(run_id: str) -> dict[str, dict]:
    return {r["cid"]: r for r in _read_jsonl(stances_path(run_id))}


def _cid(d: dict) -> str:
    return f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"


def load_decisions(run_id: str) -> list[dict]:
    """Every p6 decision in the run, in a stable order.

    p4/p5 decisions are skipped: they carry `rost` straight from the model with no
    stance in between, so there is no citations->stance->vote chain to recheck.
    """
    sim_dir = RESULTS_DIR / "simulations" / run_id
    if not sim_dir.exists():
        raise FileNotFoundError(f"no simulations for run {run_id!r}")
    out = []
    for path in sorted(sim_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("hallning") and version_ge(d.get("prompt_version", ""), "p6"):
                out.append(d)
    out.sort(key=lambda d: (d["votering_id"], d["parti"]))
    return out


def _unit(d: dict) -> dict:
    """One decision as the judge sees it — WITHOUT `hallning` or `rost`.

    Leaving either in would anchor the re-derivation and make the comparison
    circular. `tier`/`coverage` are left out for the same reason: they are derived
    from the very fields under audit.

    Key order matters here too: `citations` before `motivering`, matching the field
    order the judge answers in, so the payload does not invite it to read the
    conclusion before it has weighed the evidence.
    """
    return {
        "cid": _cid(d),
        "citations": [
            {"quote": c.get("quote", ""), "princip": c.get("princip", "")}
            for c in (d.get("citations") or [])
        ],
        "motivering": d.get("motivering", ""),
    }


def _arende(case: dict, arm: str = "anonymous") -> str | None:
    """The case exactly as the deciding agent was served it.

    Rendered through `promptgen`, not copied — so "the judge saw the same evidence"
    is true by construction and cannot drift when the renderer changes.
    """
    from aidag.promptgen import _render_p6_arende

    block = _render_p6_arende(case, arm)
    return "\n".join(block) if block else None


def _pending(run_id: str, sample: int | None = None) -> list[dict]:
    """Cases with at least one un-rechecked p6 decision, as request groups.

    Undecidable cases are skipped rather than judged: their stance refers to no
    counter-proposal, so a judge has nothing to re-derive from either, and a
    disagreement there would report a stance defect where the real defect is the
    prompt (`promptgen.p6_decidable`, `casemeta.undecidable_report`).
    """
    done = set(load_stances(run_id))
    cases = {
        c["votering_id"]: c
        for c in pl.read_parquet(PROCESSED_DIR / "cases.parquet").iter_rows(named=True)
    }
    by_case: dict[str, list[dict]] = {}
    skipped_undecidable = 0
    for d in load_decisions(run_id):
        if _cid(d) in done:
            continue
        case = cases.get(d["votering_id"])
        if case is None:
            continue
        by_case.setdefault(d["votering_id"], []).append(d)
    groups = []
    for vid in sorted(by_case):
        arende = _arende(cases[vid], by_case[vid][0].get("arm", "anonymous"))
        if arende is None:
            skipped_undecidable += len(by_case[vid])
            continue
        groups.append(
            {
                "votering_id": vid,
                "arende": arende,
                "units": [_unit(d) for d in sorted(by_case[vid], key=lambda x: x["parti"])],
            }
        )
    if skipped_undecidable:
        print(
            f"  skipped {skipped_undecidable} decisions on undecidable cases "
            f"(see `aidag undecidable-report`)"
        )
    if sample:
        # Deterministic stride, not a random draw: no seed to record, the same
        # --sample always covers the same cases, and it spreads across the whole
        # corpus by date instead of clustering in one committee.
        step = max(1, len(groups) // sample)
        groups = groups[::step][:sample]
    return groups


def prepare(
    run_id: str,
    batch_size: int = 240,
    per_request: int = CASES_PER_REQUEST,
    sample: int | None = None,
) -> None:
    """Write the NEXT recheck batch manifest from whatever is still pending.

    One manifest item = one request file = one judge agent handling `per_request`
    cases. `sample` caps the work at roughly that many cases on a deterministic
    stride, for a rate estimate before committing to the full pass.
    """
    groups = _pending(run_id, sample=sample)
    if not groups:
        print("nothing pending — recheck complete")
        return
    base = run_dir(run_id)
    (base / "reqs").mkdir(parents=True, exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)
    existing = sorted((base / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1
    packed = [groups[i : i + per_request] for i in range(0, len(groups), per_request)]
    packed = packed[:batch_size]
    out_dir = base / "out" / f"batch-{n:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for i, cases_in_req in enumerate(packed):
        req = base / "reqs" / f"batch-{n:03d}-{i:04d}.json"
        req.write_text(
            json.dumps(
                {"instructions": INSTRUCTIONS, "cases": cases_in_req},
                ensure_ascii=False,
            )
        )
        items.append(
            {"path": str(req), "n_cases": len(cases_in_req),
             "n_units": sum(len(c["units"]) for c in cases_in_req)}
        )
    manifest = base / "batches" / f"batch-{n:03d}.json"
    manifest.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "n_items": len(items),
                "prefix": f"batch-{n:03d}-",
                "req_dir": str(base / "reqs"),
                "out_dir": str(out_dir),
                "n_units": sum(it["n_units"] for it in items),
                "items": items,
            },
            ensure_ascii=False,
        )
    )
    n_units = sum(it["n_units"] for it in items)
    n_cases = sum(it["n_cases"] for it in items)
    print(f"recheck batch manifest: {manifest}")
    print(f"  {len(items)} agents, prefix batch-{n:03d}-, {n_cases} cases, {n_units} decisions")
    remaining = sum(len(g["units"]) for g in groups) - n_units
    print(f"  remaining after this batch: {remaining}")
    # Printed rather than documented: the workflow derives request filenames from
    # prefix+count instead of reading the manifest, so these four values are the
    # contract between the two halves and must not be retyped from memory.
    print("\nlaunch:")
    print(
        "  Workflow({ scriptPath: \"scripts/stance_recheck_workflow.js\", args: {\n"
        f"    reqDir: \"{base / 'reqs'}\",\n"
        f"    outDir: \"{out_dir}\",\n"
        f"    prefix: \"batch-{n:03d}-\", count: {len(items)}, runId: \"{run_id}\",\n"
        "    model: \"sonnet\" } })"
    )


def ingest(run_id: str, input_path: str, model: str) -> None:
    """Ingest judge output ({units:[...]} files, or a dir of them). Idempotent on cid.

    Only cids the run actually contains are accepted: a judge that invents or
    mangles a cid would otherwise write a verdict that no report could ever match
    against a decision, and the disagreement rate would quietly count it as agreement.
    """
    known = {_cid(d) for d in load_decisions(run_id)}
    p = Path(input_path)
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    rows, unknown, bad = [], 0, 0
    for f in files:
        data = json.loads(f.read_text())
        for u in data.get("units", []):
            if u.get("cid") not in known:
                unknown += 1
                continue
            if u.get("hallning") not in ("stodjer", "avvisar"):
                bad += 1
                continue
            rows.append({**u, "model": model,
                         "collected_at": datetime.now(timezone.utc).isoformat()})
    merged = load_stances(run_id)
    merged.update({r["cid"]: r for r in rows})
    out = stances_path(run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(merged[k], ensure_ascii=False) + "\n" for k in sorted(merged))
    )
    print(f"recheck ingest: {len(rows)} verdicts from {len(files)} files -> {out}")
    print(f"  total {len(merged)} | {unknown} unknown cid | {bad} malformed")


def status(run_id: str) -> None:
    total = len(load_decisions(run_id))
    done = len(load_stances(run_id))
    print(f"recheck ({run_id}): {total} p6 decisions | {done} rechecked | {total - done} pending")


def report(run_id: str, limit: int = 25) -> dict:
    """The three findings, with rates. Read-only."""
    from aidag.promptgen import HALLNING_TO_ROST

    stored = {_cid(d): d for d in load_decisions(run_id)}
    verdicts = load_stances(run_id)
    if not verdicts:
        print(f"no verdicts yet for {run_id} — run stance-recheck-prepare/ingest")
        return {}
    sign, contradiction, ungrounded = [], [], []
    for cid, v in verdicts.items():
        d = stored.get(cid)
        if not d:
            continue
        if v["hallning"] != d["hallning"]:
            sign.append((cid, v, d))
        if v["motivering_direction"] != "unclear" and v["motivering_direction"] != d["hallning"]:
            contradiction.append((cid, v, d))
        if v["citations_bear"] == "no":
            ungrounded.append((cid, v, d))
    n = len(verdicts)

    def _pct(k):
        return f"{k} / {n} ({100 * k / n:.2f}%)"

    print(f"recheck report ({run_id}) — {n} decisions rechecked\n")
    print(f"  sign          {_pct(len(sign))}  judge's stance disagrees with the stored one")
    print(f"  contradiction {_pct(len(contradiction))}  motivering concludes against its own stance")
    print(f"  ungrounded    {_pct(len(ungrounded))}  citations do not bear on the demand")
    # A sign disagreement the judge itself was unsure of is a weaker finding than
    # one it was confident about; the site's own claim is only as strong as the
    # confident half, so report it split rather than as one rate.
    hi = [x for x in sign if x[1]["confidence"] == "high"]
    print(f"\n  of the sign disagreements, {len(hi)} were high-confidence")
    both = [x for x in sign if x[0] in {c[0] for c in contradiction}]
    print(f"  {len(both)} decisions fail BOTH sign and contradiction — the FöU14 class")
    if both:
        print("\n  worst first (both findings):")
        for cid, v, d in both[:limit]:
            print(f"    {cid}")
            print(f"      stored {d['hallning']} -> {d['rost']} | judge {v['hallning']} -> "
                  f"{HALLNING_TO_ROST[v['hallning']]} | prose {v['motivering_direction']}")
            print(f"      {v['note'][:110]}")
    return {
        "n": n,
        "sign": [c for c, _v, _d in sign],
        "contradiction": [c for c, _v, _d in contradiction],
        "ungrounded": [c for c, _v, _d in ungrounded],
        "both": [c for c, _v, _d in both],
    }


def verify_recheck(run_id: str | None):
    """Checks for `aidag verify recheck --run-id X`. Yields (name, ok, detail)."""
    if not run_id:
        yield ("run_id provided", False, "pass --run-id")
        return
    try:
        total = len(load_decisions(run_id))
    except FileNotFoundError as e:
        yield ("simulations exist", False, str(e))
        return
    verdicts = load_stances(run_id)
    yield ("verdicts exist", bool(verdicts), f"{len(verdicts)} of {total} p6 decisions rechecked")
    if not verdicts:
        return
    stored = {_cid(d): d for d in load_decisions(run_id)}
    orphans = [c for c in verdicts if c not in stored]
    yield ("every verdict maps to a decision", not orphans, f"{len(orphans)} orphaned cids")
    # Not a pass/fail on the rate itself — the rate is the finding, and gating on a
    # threshold here would turn a measurement into a number someone tunes. What has
    # to hold is that the verdicts are well-formed and attributable.
    bad = [c for c, v in verdicts.items() if v.get("hallning") not in ("stodjer", "avvisar")]
    yield ("verdicts are well-formed", not bad, f"{len(bad)} malformed")
