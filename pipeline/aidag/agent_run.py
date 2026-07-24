"""Checkpointed batch preparation for the subagent-based full run.

The full run = 2,539 cases x 8 parties (decisions) + 2,539 probes, executed as
Claude Code subagent batches (Opus orchestrator, Sonnet vote agents) rather
than the paid Batch API. See docs/orchestration-full-run.md for the protocol.

Checkpoint model — no separate state file:
  done      = custom_ids present in data/results/simulations/{run_id}/*.jsonl
              (+ votering_ids in probes/{run_id}/probe.jsonl)
  pending   = everything else
`prepare` always emits the next batch from pending, so re-running after a
partial/failed/limit-interrupted batch automatically resumes.

Layout under data/interim/agentrun/{run_id}/:
  system/{context}.txt             party corpus + role, one per distinct context
  groups/g-NNNN.json               every case for one agent (1 read, not N)
  probes/{vid}.json                {"prompt": <probe prompt>}
  batches/batch-{NNN}.json         manifest for one workflow invocation
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from aidag.config import INTERIM_DIR, PARTY_CODES, PROCESSED_DIR, PROMPT_VERSION
from aidag.corpus import context_key
from aidag.probe import PROBE_SYSTEM, collected_probe_ids, probe_user_message
from aidag.promptgen import build_system_blocks, render_user_message
from aidag.simulate import collected_ids

ARM = "anonymous"


def run_dir(run_id: str):
    return INTERIM_DIR / "agentrun" / run_id


def _load_cases() -> list[dict]:
    return (
        pl.read_parquet(PROCESSED_DIR / "cases.parquet")
        .sort("datum", "votering_id")
        .to_dicts()
    )


def _system_filename(party: str, case: dict, prompt_version: str) -> str:
    return f"{context_key(party, case['datum'], case['rm'], case['votering_id'], prompt_version)}.txt"


# Context budget for one group agent. The agent must hold its party corpus, every
# case in the group, its own reasoning, and emit a decision per case — so the cap
# is on the whole conversation, not just the input.
CONTEXT_HEADROOM = 0.85     # leave room for the agent's reasoning turns
TOOL_SLACK = 10_000         # file reads, retries
OUTPUT_PER_CASE = 400       # motivering + citations, in tokens
# Group agents no longer return every decision in ONE structured response; they
# WRITE decisions to JSONL files in chunks (see scripts/grouped_batch_workflow.js),
# so the 24k single-response cap that pinned a group to 60 cases no longer binds —
# a chunk is a fresh Write per turn, and a merge script (aidag agent-merge) reads
# the files back. Group size is now bounded by CONTEXT (plan_groups: the corpus +
# every case + the agent's reasoning must fit the window) and by MAX_GROUP, a
# sanity ceiling so one agent never owns an unreviewably large group. OUTPUT_PER_CASE
# still enters the context budget: the agent holds what it has written so far.
MAX_GROUP = 150


def _tokens(text: str) -> int:
    """Swedish averages ~3.6 characters per token."""
    return int(len(text) / 3.6)


def plan_groups(
    system_text: str,
    members: list[tuple[str, dict]],
    context_limit: int,
    prompt_version: str = PROMPT_VERSION,
) -> list[list[tuple[str, dict]]]:
    """Split one (party, context) bucket into the FEWEST agents that each fit.

    Group size is decided by size, not by a fixed number: a party's corpus runs
    53k-126k tokens under p5 (17-32k words under p6) and is read once per agent,
    so group size IS the cost lever — a 2-case agent costs ~25k tokens per
    decision, a 90-case agent ~1.7k. Cases are split into EQUAL parallel groups
    rather than packing full agents and leaving a stub.

    `prompt_version` must match the version the batch will actually run: the
    per-case cost is the rendered user message, and p6 renders a different
    <arende> block than p5. Defaulting it silently sized p6 groups with p5
    lengths.
    """
    budget = context_limit * CONTEXT_HEADROOM - _tokens(system_text) - TOOL_SLACK
    # a case costs its user message plus the decision the agent must write back
    costs = [
        _tokens(render_user_message(case, arm=ARM, prompt_version=prompt_version))
        + OUTPUT_PER_CASE
        for _cid, case in members
    ]
    total = sum(costs)
    if budget <= 0:
        raise ValueError("party corpus alone exceeds the context budget")
    # bounded by BOTH limits: context (the corpus + cases must fit) and output
    # (every decision comes back in one response)
    by_context = -(-int(total) // int(budget))                 # ceil
    by_output = -(-len(members) // MAX_GROUP)                  # ceil
    n_groups = max(1, by_context, by_output)
    target = total / n_groups                                  # balance, don't fill-and-stub

    groups: list[list[tuple[str, dict]]] = [[]]
    running = 0.0
    for member, cost in zip(members, costs):
        over_target = groups[-1] and running + cost > target
        over_cap = len(groups[-1]) >= MAX_GROUP
        if (over_target and len(groups) < n_groups) or over_cap:
            groups.append([])
            running = 0.0
        groups[-1].append(member)
        running += cost
    return groups


def _decision_of(cid: str) -> tuple[str, str]:
    """(party, votering_id) — the identity of a decision, independent of which
    prompt version or arm produced it. Mirroring and cross-run comparison key on
    this, because an experiment's whole point is to vary prompt or arm."""
    party, vid, _prompt, _arm = cid.split(":")
    return party, vid


def _pending(
    run_id: str,
    cases: list[dict],
    include_probes: bool,
    mirror_run: str | None = None,
    prompt_version: str = PROMPT_VERSION,
):
    """Pending work. With mirror_run, the universe is restricted to the decisions
    already COLLECTED in that run — used for methodology experiments that re-run
    the same decisions under a different execution design, prompt or corpus."""
    done = set()
    for party in PARTY_CODES:
        done |= collected_ids(run_id, party)
    universe = None
    if mirror_run:
        universe = set()
        for party in PARTY_CODES:
            universe |= {_decision_of(c) for c in collected_ids(mirror_run, party)}
    sims = []
    for case in cases:
        for party in PARTY_CODES:
            cid = f"{party}:{case['votering_id']}:{prompt_version}:{ARM}"
            if cid in done:
                continue
            if universe is not None and (party, case["votering_id"]) not in universe:
                continue
            sims.append((cid, party, case))
    probes = []
    if include_probes and mirror_run is None:
        probes_done = collected_probe_ids(run_id)
        probes = [c for c in cases if c["votering_id"] not in probes_done]
    return sims, probes


def prepare(
    run_id: str,
    batch_size: int = 240,
    include_probes: bool = True,
    group: int = 1,
    mirror_run: str | None = None,
    prompt_version: str = PROMPT_VERSION,
    context_limit: int = 200_000,
) -> None:
    """Write the NEXT batch manifest from whatever is still pending.

    group > 1 packs N cases per agent; group == 0 packs by size, filling each
    agent up to --context-limit (and the output cap). Cases are bucketed by
    (party, context) — one agent then decides several cases while reading the
    party corpus once (see scripts/grouped_batch_workflow.js)."""
    base = run_dir(run_id)
    cases = _load_cases()
    sims, probes = _pending(
        run_id, cases, include_probes, mirror_run=mirror_run, prompt_version=prompt_version
    )
    if not sims and not probes:
        print("nothing pending — run complete")
        return

    (base / "system").mkdir(parents=True, exist_ok=True)
    (base / "cases").mkdir(exist_ok=True)
    (base / "probes").mkdir(exist_ok=True)
    (base / "batches").mkdir(exist_ok=True)
    (base / "groups").mkdir(exist_ok=True)

    # Batch number is derived up front so the group agents' output directory can be
    # scoped to this batch. Group filenames (g-NNNN) restart at 0 every prepare, so
    # an un-scoped out dir would let a later batch's merge pick up stale files.
    existing = sorted((base / "batches").glob("batch-*.json"))
    n = int(existing[-1].stem.split("-")[1]) + 1 if existing else 1
    out_dir = base / "out" / f"batch-{n:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    def _system_file(party: str, case: dict) -> str:
        """One file per distinct context (12 under p4; 34 under p5, since the
        programme and the shadow budget roll over on their adoption dates)."""
        name = _system_filename(party, case, prompt_version)
        path = base / "system" / name
        if not path.exists():
            blocks = build_system_blocks(
                party, case["datum"], case["rm"], case["votering_id"], prompt_version
            )
            path.write_text("\n\n".join(b["text"] for b in blocks))
        return name

    batch_sims = sims[:batch_size]
    # probes (cheap) fill whatever room decisions leave, so probe batches naturally
    # run once all decisions are collected
    room = max(0, batch_size - len(batch_sims))
    batch_probes = probes[:room]

    def _case_file(case: dict):
        # version-scoped like the system file: the rendered <arende> differs
        # between p5 and p6, so an unversioned name would serve stale case text
        # if a run were re-prepared at a different prompt version.
        path = base / "cases" / f"{case['votering_id']}-{prompt_version}.json"
        if not path.exists():
            path.write_text(
                json.dumps(
                    {"user": render_user_message(case, arm=ARM, prompt_version=prompt_version)},
                    ensure_ascii=False,
                )
            )
        return path

    items = []
    if group != 1:
        # Grouped by (party, context) — NOT by month. The system file already
        # encodes every date-dependent difference (which programme version stands,
        # whether Tidöavtalet applies, which shadow budget is live), so any two
        # cases sharing a context can share an agent whatever month they fall in,
        # and each case carries its own date and worldstate in its own text.
        #
        # Adding month to the key only fragments the work: it produced 2-case
        # agents paying the full ~50k corpus prefix — ~25k tokens per decision,
        # against ~7.5k for a 21-case agent. Group size is the whole cost lever.
        buckets: dict[tuple, list] = {}
        for cid, party, case in batch_sims:
            key = (party, _system_file(party, case))
            buckets.setdefault(key, []).append((cid, case))
        # compact on purpose: party/vid/case_file derive from the cid + the
        # manifest-level dirs, so the workflow's loader agent can transcribe
        # a large manifest within its output-token limit
        for (party, system_name), members in sorted(buckets.items()):
            if group > 0:
                chunks = [members[i : i + group] for i in range(0, len(members), group)]
            else:
                # group=0: size-driven. Fit the whole party-month in one agent if
                # it fits the window; otherwise split into equal parallel groups.
                system_text = (base / "system" / system_name).read_text()
                chunks = plan_groups(system_text, members, context_limit, prompt_version)
            for chunk in chunks:
                # One file per group, not one per case: the agent reads its
                # corpus once and its cases once, so a 30-case agent makes 2 tool
                # calls instead of 31. Measured at ~2 tool calls per case before
                # this, and that turn overhead — not the case text (768 tokens) —
                # is most of the 7.2k tokens a decision costs.
                gf = f"g-{len(items):04d}.json"
                (base / "groups" / gf).write_text(
                    json.dumps(
                        {
                            "cases": [
                                {"cid": cid,
                                 "user": render_user_message(
                                     case, arm=ARM, prompt_version=prompt_version)}
                                for cid, case in chunk
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
                items.append({
                    "kind": "simgroup",
                    "party": party,
                    "sys": system_name,
                    "gf": gf,
                    "cids": [cid for cid, _case in chunk],
                })
    else:
        for cid, party, case in batch_sims:
            _case_file(case)
            items.append({
                "kind": "sim",
                "cid": cid,
                "sys": _system_file(party, case),
            })
    for case in batch_probes:
        probe_file = base / "probes" / f"{case['votering_id']}.json"
        if not probe_file.exists():
            probe_file.write_text(
                json.dumps(
                    {"prompt": PROBE_SYSTEM + "\n\n" + probe_user_message(case)},
                    ensure_ascii=False,
                )
            )
        items.append({"kind": "probe", "vid": case["votering_id"]})

    manifest_path = base / "batches" / f"batch-{n:03d}.json"
    n_sims = sum(
        len(i["cids"]) if i["kind"] == "simgroup" else 1
        for i in items
        if i["kind"] in ("sim", "simgroup")
    )
    n_probes = sum(1 for i in items if i["kind"] == "probe")
    # n_sims/n_probes let the workflow script verify the manifest survived the
    # loader agent's transcription intact (it re-counts and compares)
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "prompt_version": prompt_version,
                "n_sims": n_sims,
                "n_probes": n_probes,
                "cases_dir": str(base / "cases"),
                "groups_dir": str(base / "groups"),
                "probes_dir": str(base / "probes"),
                "system_dir": str(base / "system"),
                "out_dir": str(out_dir),
                "items": items,
            },
            ensure_ascii=False,
        )
    )
    print(f"batch manifest: {manifest_path}")
    print(f"  {n_sims} decisions + {n_probes} probes in this batch")
    print(f"  remaining after this batch: {len(sims) - n_sims} decisions, {len(probes) - n_probes} probes")


def status(run_id: str) -> None:
    cases = _load_cases()
    sims, probes = _pending(run_id, cases, include_probes=True)
    total_sims = len(cases) * len(PARTY_CODES)
    done_sims = total_sims - len(sims)
    done_probes = len(cases) - len(probes)
    print(f"run {run_id}:")
    print(f"  decisions: {done_sims}/{total_sims} done ({done_sims / total_sims:.1%}), {len(sims)} pending")
    print(f"  probes:    {done_probes}/{len(cases)} done, {len(probes)} pending")
    if sims:
        # temporal frontier: the batches walk chronologically
        print(f"  next pending case date: {sims[0][2]['datum']}")
    batches = sorted((run_dir(run_id) / "batches").glob("batch-*.json"))
    print(f"  batch manifests issued: {len(batches)}")


def _load_decisions(path: Path) -> list[dict]:
    """Parse one out-file into a list of decision dicts. Liberal in what it
    accepts — a group agent may have written JSONL (one object per line), a JSON
    array, or a single object — and tolerant of a truncated final line, so a
    partial write still yields every complete decision it holds."""
    text = path.read_text()
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return [d for d in obj if isinstance(d, dict)]
        if isinstance(obj, dict) and isinstance(obj.get("decisions"), list):
            return [d for d in obj["decisions"] if isinstance(d, dict)]
        if isinstance(obj, dict) and "cid" in obj:
            return [obj]
    except json.JSONDecodeError:
        pass
    out: list[dict] = []
    for line in text.splitlines():
        s = line.strip().rstrip(",")
        if not s or s in "[]{}":
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue  # truncated / malformed line — the cid re-issues next prepare
        if isinstance(d, dict):
            out.append(d)
    return out


def merge(
    run_id: str,
    manifest_path: str | None = None,
    probes_path: str | None = None,
    out_path: str | None = None,
) -> str:
    """Collect the JSONL/JSON decision files the group agents wrote for a batch
    into the single {run_id, sims, probes} payload `agent-ingest` consumes.

    Files live under manifest['out_dir'] (scoped per batch). Each group's files
    are named g-NNNN-*.jsonl. A decision keeps only cids the manifest issued to
    that group (a stray/hallucinated cid is dropped and stays pending); dedupes
    on cid. Whatever an agent failed to write is simply absent — the next
    `agent-prepare` re-issues exactly those cids, so a partial batch is safe."""
    base = run_dir(run_id)
    if manifest_path:
        mpath = Path(manifest_path)
    else:
        batches = sorted((base / "batches").glob("batch-*.json"))
        if not batches:
            raise FileNotFoundError(f"no batch manifests for run {run_id}")
        mpath = batches[-1]
    manifest = json.loads(mpath.read_text())
    out_dir = Path(manifest["out_dir"])

    groups = [i for i in manifest["items"] if i.get("kind") == "simgroup"]
    sims: list[dict] = []
    seen: set[str] = set()
    n_expected = n_unknown = n_dup = 0
    missing_by_group: list[tuple[str, int]] = []
    for item in groups:
        allowed = set(item["cids"])
        n_expected += len(allowed)
        stem = item["gf"][:-5] if item["gf"].endswith(".json") else item["gf"]
        got: set[str] = set()
        parts = sorted(out_dir.glob(f"{stem}-*.jsonl")) + sorted(out_dir.glob(f"{stem}-*.json"))
        for p in parts:
            for dec in _load_decisions(p):
                cid = dec.get("cid")
                if cid not in allowed:
                    n_unknown += 1
                    continue
                if cid in seen:
                    n_dup += 1
                    continue
                seen.add(cid)
                got.add(cid)
                party, vid = cid.split(":")[0], cid.split(":")[1]
                sims.append({
                    "cid": cid,
                    "party": party,
                    "vid": vid,
                    "decision": {k: v for k, v in dec.items() if k != "cid"},
                })
        if allowed - got:
            missing_by_group.append((stem, len(allowed - got)))

    probes: list[dict] = []
    if probes_path:
        probes = json.loads(Path(probes_path).read_text()).get("probes", [])

    out = Path(out_path) if out_path else base / f"{mpath.stem}-merged.json"
    out.write_text(
        json.dumps({"run_id": manifest["run_id"], "sims": sims, "probes": probes}, ensure_ascii=False)
    )
    print(f"merged {len(sims)}/{n_expected} decisions from {len(groups)} groups -> {out}")
    if n_unknown:
        print(f"  {n_unknown} decisions with cids not in this batch (dropped, stay pending)")
    if n_dup:
        print(f"  {n_dup} duplicate cids (dropped)")
    if n_expected - len(sims):
        head = ", ".join(f"{s}:{m}" for s, m in missing_by_group[:10])
        print(f"  {n_expected - len(sims)} missing (re-issue on next prepare): {head}")
    if probes:
        print(f"  + {len(probes)} probes")
    return str(out)
