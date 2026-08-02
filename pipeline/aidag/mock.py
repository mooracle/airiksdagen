"""Generate fake simulation results for site development — zero API cost.

`uv run python -m aidag.mock --run-id mock-v1` writes seeded pseudo-decisions:
mostly agreeing with the actual position, with deliberate noise, so every site
state (agree/disagree/Avstår/low-confidence/not_covered) is exercised.
Mock runs are for local dev only; keep run_id prefixed 'mock' (gitignored).
"""

from __future__ import annotations

import argparse
import json
import random

import polars as pl

from aidag.config import PARTY_CODES, PROCESSED_DIR, RESULTS_DIR
from aidag.models import Decision


def run(run_id: str = "mock-v1", seed: int = 7) -> None:
    assert run_id.startswith("mock"), "mock runs must use a 'mock' run_id prefix"
    cases = pl.read_parquet(PROCESSED_DIR / "cases.parquet")
    positions = pl.read_parquet(PROCESSED_DIR / "party_positions.parquet")
    pos = {(r["votering_id"], r["parti"]): r["position"] for r in positions.iter_rows(named=True)}
    rng = random.Random(seed)

    sim_dir = RESULTS_DIR / "simulations" / run_id
    sim_dir.mkdir(parents=True, exist_ok=True)

    # real manifesto substrings so `verify simulate` stays green on mock runs
    # (the [MOCKDATA] motivering is what marks the decision as fake)
    from aidag.promptgen import _corpus_text  # noqa: PLC2701

    manifesto_snippets = {}
    for p in PARTY_CODES:
        words = " ".join(_corpus_text(f"valmanifest-2022-{p.lower()}.txt").split()).split(" ")
        manifesto_snippets[p] = " ".join(words[100:120])

    # mock English decision translations exercise the EN site path; case-text
    # translations are deliberately left missing to exercise the fallback
    trans_dir = RESULTS_DIR / "translations" / run_id
    trans_dir.mkdir(parents=True, exist_ok=True)
    trans_f = open(trans_dir / "decisions.jsonl", "w")

    for party in PARTY_CODES:
        with open(sim_dir / f"{party}.jsonl", "w") as f:
            for case in cases.iter_rows(named=True):
                actual = pos.get((case["votering_id"], party))
                if actual in (None, "Frånvarande"):
                    actual = rng.choice(["Ja", "Nej"])
                roll = rng.random()
                if roll < 0.72:
                    rost = actual
                elif roll < 0.88:
                    rost = rng.choice([v for v in ["Ja", "Nej", "Avstår"] if v != actual])
                else:
                    rost = "Avstår"
                coverage = rng.choices(
                    ["explicit", "inferred", "not_covered"], weights=[35, 45, 20]
                )[0]
                decision = Decision(
                    votering_id=case["votering_id"],
                    parti=party,
                    run_id=run_id,
                    prompt_version="mock",
                    model="mock-model",
                    arm="anonymous",
                    rost=rost,  # type: ignore[arg-type]
                    confidence=rng.choices(["high", "medium", "low"], weights=[4, 4, 2])[0],  # type: ignore[arg-type]
                    coverage=coverage,  # type: ignore[arg-type]
                    motivering=(
                        f"[MOCKDATA] Simulerad motivering för {party} i ärendet "
                        f"'{case['rubrik']}'. Ersätts av verklig AI-motivering."
                    ),
                    citations=[{"document": "valmanifest", "quote": manifesto_snippets[party]}],
                    flags=(["tido_conflict"] if rng.random() < 0.05 else []),
                )
                f.write(decision.model_dump_json() + "\n")
                trans_f.write(json.dumps({
                    "cid": f"{party}:{case['votering_id']}:mock:anonymous",
                    "motivering": (
                        f"[MOCKDATA EN] Simulated reasoning for {party} on "
                        f"'{case['rubrik']}'. Replaced by a real AI translation."
                    ),
                    "citations": [{"quote": f"[EN] {manifesto_snippets[party]}", "princip": ""}],
                    "omvarld": [],
                    "model": "mock-model",
                    "collected_at": "",
                }, ensure_ascii=False) + "\n")
    trans_f.close()

    print(f"mock run '{run_id}': {len(cases)} cases x {len(PARTY_CODES)} parties")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="mock-v1")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    run(run_id=args.run_id, seed=args.seed)
