"""Recall probe — does the model know WHICH vote it is looking at?

The leakage controls strip case numbers, vote ids and exact dates, but the p6
prompt still shows the month, the committee and the case title, and those three
fields alone identify 2,499 of the 2,539 votes. Whether that matters is an
empirical question, so this module measures it instead of arguing it away
(after Golchin & Surdeanu, "Time Travel in LLMs", ICLR 2024).

The model gets ONLY those three fields and must guess, with no "don't know":

  - the committee report's number (7 in 2023/24:FöU7). The topic cannot reveal
    it, so hits above chance are memory of Riksdag documents, nothing else;
  - the parties behind the reservation. The topic partly reveals these (a climate
    reservation is likely MP), so they are scored against a leave-one-out
    baseline: the committee's most common author set among the other sampled votes.

Forced guessing is deliberate: allowing abstention hides weak recall behind
"don't know" and leaves nothing to test against chance.

The probe runs through the Claude Code CLI, the same tooling as the full run,
isolated: our own one-line system prompt, no tools, no MCP servers, hooks
disabled, from an empty directory, no session saved. Questions go 25 to a call
— each call carries a fixed overhead, and 150 single calls cost several times
the questions themselves.

    uv run aidag recall-probe-sample            # data/results/probes/sample.json
    uv run aidag recall-probe-run --model claude-opus-5
    uv run aidag recall-probe-report --run-id full-v4
"""

from __future__ import annotations

import csv
import json
import random
import re
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import comb
from pathlib import Path

from aidag.config import PARTY_CODES, PROCESSED_DIR, REPO_ROOT, RESULTS_DIR
from aidag.promptgen import HALLNING_TO_ROST, SV_MONTH_NAMES, p6_decidable

PROBE_DIR = RESULTS_DIR / "probes"
SAMPLE_PATH = PROBE_DIR / "sample.json"
SEED = 20261003
N_SAMPLE = 150
BATCH = 25

PROMPT = """Detta gäller en votering i Sveriges riksdag.
Tidpunkt: {month}.
Utskott: {utskott}
Ärende: {rubrik}

Svara utifrån vad du vet om just denna votering. Ge alltid ditt bästa svar, även om du är osäker.
1. Vilket nummer hade utskottets betänkande? Ange bara numret efter utskottsförkortningen (t.ex. 7 för {utskott}7).
2. Vilka partier stod bakom reservationen (motförslaget) som ställdes mot utskottets förslag? Ange minst ett parti bland S, M, SD, C, V, KD, MP, L.
3. Hur säker är du (high, medium, low)?"""

ANSWER = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "betankande_nummer", "reservation_partier", "confidence"],
    "properties": {
        "id": {"type": "string"},
        "betankande_nummer": {"type": "integer"},
        "reservation_partier": {"type": "array", "items": {"type": "string", "enum": list(PARTY_CODES)}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
BATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"],
                "properties": {"answers": {"type": "array", "items": ANSWER}}}


# --- inputs -----------------------------------------------------------------

def load_cases() -> dict[str, dict]:
    """Case rows keyed by votering_id. The processed parquet when it has been
    built; otherwise the committed open download, so the probe runs on a fresh
    clone without re-fetching the Riksdag."""
    pq = PROCESSED_DIR / "cases.parquet"
    if pq.exists():
        import polars as pl

        return {r["votering_id"]: r for r in pl.read_parquet(pq).to_dicts()}
    path = REPO_ROOT / "site/public/downloads/cases.csv"
    return {r["votering_id"]: r for r in csv.DictReader(path.open())}


def load_positions() -> dict[tuple[str, str], str]:
    pq = PROCESSED_DIR / "party_positions.parquet"
    if pq.exists():
        import polars as pl

        return {(r["votering_id"], r["parti"]): r["position"] for r in pl.read_parquet(pq).to_dicts()}
    path = REPO_ROOT / "site/public/downloads/party_positions.csv"
    return {(r["votering_id"], r["parti"]): r["position"] for r in csv.DictReader(path.open())}


def reservation_authors() -> dict[str, set[str]]:
    out = {}
    for line in (RESULTS_DIR / "casemeta" / "cases.jsonl").open():
        r = json.loads(line)
        out[r["votering_id"]] = set(r.get("parties_involved") or [])
    return out


def run_decisions(run_id: str) -> dict[tuple[str, str], str]:
    """(votering_id, party) -> the vote a run's decision implies."""
    out = {}
    for f in (RESULTS_DIR / "simulations" / run_id).glob("*.jsonl"):
        for line in f.open():
            d = json.loads(line)
            vote = HALLNING_TO_ROST.get(d.get("hallning")) or d.get("rost")
            out[(d["votering_id"], d["parti"])] = vote
    return out


def report_number(beteckning: str) -> int | None:
    m = re.search(r"(\d+)$", beteckning or "")
    return int(m.group(1)) if m else None


def month_sv(datum: str) -> str:
    return f"{SV_MONTH_NAMES[int(datum[5:7]) - 1]} {datum[:4]}"


# --- sample -----------------------------------------------------------------

def half_year(datum: str) -> str:
    return datum[:4] + ("H1" if int(datum[5:7]) <= 6 else "H2")


def sample(n: int = N_SAMPLE, seed: int = SEED, run_id: str = "full-v4") -> list[str]:
    """Votes stratified by half-year, largest-remainder allocation, fixed seed.

    The pool is every p6-decidable vote with a decision for all eight parties in
    `run_id`, so the sample also serves a no-documents arm on the same votes."""
    cases = load_cases()
    have = defaultdict(set)
    for vid, party in run_decisions(run_id):
        have[vid].add(party)
    pool = [c for v, c in cases.items() if len(have[v]) == len(PARTY_CODES) and p6_decidable(c, "anonymous")]
    strata = defaultdict(list)
    for c in sorted(pool, key=lambda c: c["votering_id"]):
        strata[half_year(str(c["datum"]))].append(c)
    quota = {k: n * len(v) / len(pool) for k, v in strata.items()}
    alloc = {k: int(q) for k, q in quota.items()}
    for k in sorted(quota, key=lambda k: quota[k] - alloc[k], reverse=True)[: n - sum(alloc.values())]:
        alloc[k] += 1
    rng = random.Random(seed)
    chosen = []
    for key in sorted(strata):
        chosen += rng.sample(strata[key], alloc[key])
    return [c["votering_id"] for c in chosen]


def write_sample(n: int = N_SAMPLE, seed: int = SEED, run_id: str = "full-v4") -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    vids = sample(n, seed, run_id)
    SAMPLE_PATH.write_text(json.dumps({"seed": seed, "n": n, "pool_run": run_id, "votering_ids": vids}, indent=1))
    print(f"{SAMPLE_PATH}: {len(vids)} votes")


# --- run --------------------------------------------------------------------

def _claude(user: str, model: str) -> dict:
    """One isolated Claude Code CLI call with structured output."""
    work = Path(tempfile.mkdtemp(prefix="aidag-probe-"))
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
           "--strict-mcp-config", "--no-session-persistence",
           "--settings", '{"disableAllHooks": true}', "--exclude-dynamic-system-prompt-sections",
           "--system-prompt", "You are a helpful assistant.", "--json-schema", json.dumps(BATCH_SCHEMA)]
    p = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=work, timeout=3600)
    d = json.loads(p.stdout)
    if d.get("is_error") or d.get("structured_output") is None:
        raise RuntimeError(f"claude CLI failed: {str(d.get('result'))[:300]}")
    usage = (d.get("modelUsage") or {}).get(model, {})
    return {"answers": d["structured_output"]["answers"], "cost_usd": usage.get("costUSD"),
            "models": list((d.get("modelUsage") or {}).keys())}


def out_path(model: str) -> Path:
    return PROBE_DIR / f"recall-{model}.jsonl"


def run(model: str = "claude-opus-5") -> None:
    """Checkpointed: votes already answered in the output file are skipped."""
    vids = json.loads(SAMPLE_PATH.read_text())["votering_ids"]
    cases = load_cases()
    out = out_path(model)
    done = {json.loads(line)["votering_id"] for line in out.open()} if out.exists() else set()
    pending = [v for v in vids if v not in done]
    print(f"{len(vids)} votes, {len(done)} answered, asking {len(pending)} of {model}")
    for i in range(0, len(pending), BATCH):
        chunk = pending[i:i + BATCH]
        qs = "\n\n".join(
            f"### Fråga {v}\n" + PROMPT.format(month=month_sv(str(cases[v]["datum"])),
                                               utskott=cases[v]["utskott"], rubrik=cases[v]["rubrik"])
            for v in chunk)
        user = (f"Nedan följer {len(chunk)} separata frågor, var och en om en egen votering. Besvara varje "
                "fråga för sig. Svara med ett objekt per fråga i \"answers\", med frågans id i \"id\".\n\n" + qs)
        res = _claude(user, model)
        if model not in res["models"]:
            raise RuntimeError(f"asked for {model}, served by {res['models']}")
        answers = {a["id"]: a for a in res["answers"]}
        now = datetime.now(timezone.utc).isoformat()
        with out.open("a") as f:
            for v in chunk:
                if v not in answers:
                    continue  # re-asked on the next run
                a = {k: val for k, val in answers[v].items() if k != "id"}
                f.write(json.dumps({"votering_id": v, "model": model, "answer": a,
                                    "batch_cost_usd": res["cost_usd"], "batch_n": len(chunk),
                                    "collected_at": now}, ensure_ascii=False) + "\n")
        print(f"  {min(i + BATCH, len(pending))}/{len(pending)}  batch cost ${res['cost_usd']}")
        time.sleep(1)


# --- report -----------------------------------------------------------------

def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k), X ~ Binomial(n, p)."""
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def chance_of_report_number(vids: list[str], cases: dict[str, dict]) -> float:
    """Mean probability of a uniform guess over the committee's reports in that
    session, taking the highest report number seen as the count of reports."""
    top = defaultdict(int)
    for c in cases.values():
        if (n := report_number(c["beteckning"])):
            top[(c["rm"], c["utskott"])] = max(top[(c["rm"], c["utskott"])], n)
    return sum(1 / top[(cases[v]["rm"], cases[v]["utskott"])] for v in vids) / len(vids)


def report(model: str = "claude-opus-5", run_id: str | None = "full-v4") -> dict:
    vids = json.loads(SAMPLE_PATH.read_text())["votering_ids"]
    cases, authors = load_cases(), reservation_authors()
    rows = {}
    for line in out_path(model).open():
        r = json.loads(line)
        rows.setdefault(r["votering_id"], r["answer"])
    answered = [v for v in vids if v in rows]
    hits = [v for v in answered if rows[v]["betankande_nummer"] == report_number(cases[v]["beteckning"])]
    chance = chance_of_report_number(vids, cases)

    by_comm = defaultdict(list)
    for v in vids:
        by_comm[cases[v]["utskott"]].append(v)
    base = []
    for v in vids:
        others = [frozenset(authors.get(o, set())) for o in by_comm[cases[v]["utskott"]] if o != v]
        guess = Counter(others).most_common(1)[0][0] if others else frozenset()
        base.append(jaccard(set(guess), authors.get(v, set())))
    jac = [jaccard(set(rows[v]["reservation_partier"]), authors.get(v, set())) for v in answered]

    by_year = defaultdict(lambda: [0, 0])
    for v in answered:
        y = str(cases[v]["datum"])[:4]
        by_year[y][0] += 1
        by_year[y][1] += v in hits

    out = {
        "model": model, "votes": len(vids), "answered": len(answered),
        "report_number": {"hits": len(hits), "rate": round(len(hits) / len(answered), 4),
                          "chance": round(chance, 4), "p_value": binomial_tail(len(hits), len(answered), chance)},
        "authors": {"jaccard": round(sum(jac) / len(jac), 4), "baseline_jaccard": round(sum(base) / len(base), 4),
                    "exact": round(sum(set(rows[v]["reservation_partier"]) == authors.get(v, set())
                                       for v in answered) / len(answered), 4)},
        "by_year": {y: {"votes": n, "hits": h} for y, (n, h) in sorted(by_year.items())},
    }
    if run_id:
        # Do the published verdicts look different on the votes the model recognises?
        decisions, actual = run_decisions(run_id), load_positions()
        for name, group in (("recognised", hits), ("other", [v for v in answered if v not in hits])):
            pairs = [(v, p) for v in group for p in PARTY_CODES
                     if actual.get((v, p)) in ("Ja", "Nej") and (v, p) in decisions]
            out.setdefault("run_agreement", {})[name] = {
                "votes": len(group), "pairs": len(pairs),
                "agree_actual": round(sum(decisions[k] == actual[k] for k in pairs) / len(pairs), 4) if pairs else None}
    print(json.dumps(out, indent=1))
    return out
