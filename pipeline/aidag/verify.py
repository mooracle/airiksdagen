"""Read-only integrity checks per pipeline stage. Exit code 0 = pass.

Every check prints PASS/FAIL; failures accumulate and set the exit code so CI
can gate on `aidag verify all`.
"""

from __future__ import annotations

import calendar
import json

import polars as pl

from aidag.config import KB_DIR, PARTY_CODES, PROCESSED_DIR, RESULTS_DIR, RIKSMOTEN, VOTE_VALUES

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


# Expected votering counts per riksmöte (±2% tolerance; 2025/26 still growing).
EXPECTED_VOTERINGAR = {"2022/23": 564, "2023/24": 594, "2024/25": 656, "2025/26": 766}


def verify_votes() -> None:
    print("votes:")
    df = pl.read_parquet(PROCESSED_DIR / "votes.parquet")
    check("rost values in enum", df["rost"].is_in(VOTE_VALUES).all())
    check("votering_id looks like UUID", df["votering_id"].str.len_chars().eq(36).all())

    per_vot = df.filter(pl.col("avser") == "sakfrågan").group_by("votering_id").len()
    check(
        "rows per votering <= 349",
        (per_vot["len"] <= 349).all(),
        f"max={per_vot['len'].max()}",
    )
    counts = dict(
        df.group_by("rm").agg(pl.col("votering_id").n_unique()).iter_rows()
    )
    for rm in RIKSMOTEN:
        expected = EXPECTED_VOTERINGAR[rm]
        actual = counts.get(rm, 0)
        check(
            f"votering count {rm}",
            abs(actual - expected) <= max(2, int(expected * 0.02)),
            f"{actual} vs ~{expected}",
        )


def verify_cases() -> None:
    print("cases:")
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    orphans = json.loads((PROCESSED_DIR / "orphans.json").read_text())

    check("orphan rate < 3%", len(orphans) < 0.03 * (len(cases) + len(orphans)), f"{len(orphans)} orphans")
    check("forslag_text non-empty", (cases["forslag_text"].str.len_chars() > 0).all())
    check("every case has kb_month", cases["kb_month"].str.len_chars().eq(7).all())
    check(
        "positions cover all cases",
        set(positions["votering_id"]) == set(cases["votering_id"]),
    )
    check("position values valid", positions["position"].is_in(VOTE_VALUES).all())
    check("party codes valid", positions["parti"].is_in(PARTY_CODES).all())
    seat_sum = positions.group_by("votering_id").agg(pl.col("seats").sum())
    check(
        "party seats sum <= 349 per votering",
        (seat_sum["seats"] <= 349).all(),
        f"max={seat_sum['seats'].max()}",
    )
    # Cross-check our aggregation against the official per-party tables where present.
    from aidag.verify_crosscheck import crosscheck_positions

    n_checked, n_mismatch = crosscheck_positions(cases, positions)
    check(
        "aggregation matches official party tables",
        n_mismatch == 0,
        f"{n_checked} voteringar cross-checked, {n_mismatch} mismatches",
    )


def verify_kb() -> None:
    print("kb:")
    snapshots = sorted(KB_DIR.glob("*.json"))
    check("snapshots exist", len(snapshots) > 0, f"{len(snapshots)} files")
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    months = {p.stem for p in snapshots}
    needed = set(cases["kb_month"].unique())
    check("every case month has a snapshot", needed <= months, f"missing: {sorted(needed - months)[:5]}")
    for path in snapshots:
        snap = json.loads(path.read_text())
        month = snap["month"]
        last_day = f"{month}-{calendar.monthrange(int(month[:4]), int(month[5:7]))[1]:02d}"
        bad_vintage = [
            i["series"] for i in snap.get("indicators", []) if i["vintage_date"] > last_day
        ]
        bad_events = [e["date"] for e in snap.get("events", []) if e["date"] > last_day]
        if bad_vintage or bad_events:
            check(f"point-in-time {month}", False, f"future vintages {bad_vintage} events {bad_events[:3]}")
    check("point-in-time rule holds", all(f.split()[0] != "point-in-time" for f in _failures))


def verify_prompts() -> None:
    print("prompts: (covered by pytest golden tests)")
    import subprocess

    r = subprocess.run(["uv", "run", "pytest", "tests/test_promptgen.py", "-q"], capture_output=True, text=True)
    check("prompt golden tests", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout else "")


def verify_simulate(run_id: str | None) -> None:
    print(f"simulate (run_id={run_id}):")
    if not run_id:
        check("run_id provided", False, "pass --run-id")
        return
    from aidag.simulate import verify_run

    for name, ok, detail in verify_run(run_id):
        check(name, ok, detail)


def verify_site() -> None:
    print("site:")
    from aidag.config import SITE_DATA_DIR

    index = SITE_DATA_DIR / "index" / "cases-index.json"
    check("cases-index exists", index.exists())
    if index.exists():
        idx = json.loads(index.read_text())
        n_case_files = len(list((SITE_DATA_DIR / "cases").glob("*.json")))
        check("case files match index", n_case_files == len(idx), f"{n_case_files} files vs {len(idx)} index rows")


def run(stage: str, run_id: str | None = None) -> int:
    _failures.clear()
    stages = {
        "votes": verify_votes,
        "cases": verify_cases,
        "kb": verify_kb,
        "prompts": verify_prompts,
        "site": verify_site,
    }
    if stage == "simulate":
        verify_simulate(run_id)
    elif stage == "all":
        for fn in stages.values():
            try:
                fn()
            except FileNotFoundError as e:
                check(f"{fn.__name__} inputs present", False, str(e))
    elif stage in stages:
        stages[stage]()
    else:
        print(f"unknown stage: {stage}")
        return 2
    print(f"{'OK' if not _failures else 'FAILED'}: {len(_failures)} failing checks")
    return 1 if _failures else 0
