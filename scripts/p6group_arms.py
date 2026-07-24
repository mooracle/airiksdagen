#!/usr/bin/env python
"""Score arbitrary p6group arms against the one-by-one gold read.

`p6group_report.py` is hardwired to the original three execution shapes. This
one takes any list of grouped-arm output directories, so a new model / effort /
prompt arm can be put on the same ruler as the arms already measured:

  uv run python scripts/p6group_arms.py out_g20=opus4.8 out_g20_o5high=o5-high

Each ARG is `dir` or `dir=label`. The gold arm is `out_ind` (one fresh agent per
case), which is what §5-§6 of docs/grouping-cost-quality-study.md compare to.
Party M votes Ja 2,530/2,539, so vote accuracy is not scored here — on an
all-Ja set it measures nothing. Pair with `agent_meter.py` for cost and time.

Two rules worth stating, because both are judgement calls:
  * verbatim = the quote, whitespace-normalised and lowercased, is an exact
    substring of the system file the agent was actually served. A miss is an
    unusable citation: the claim cannot be checked.
  * decisive-citation agreement = arm's citations[0] and gold's citations[0]
    overlap (either quote contains the other). Exact-span equality is too
    strict to be meaningful — the models pick different boundaries around the
    same sentence — so the looser rule is the honest one. Both are printed.
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

G = Path(__file__).resolve().parents[1] / "data" / "interim" / "p6group"
REQ = {"hallning", "confidence", "coverage", "motivering", "citations", "omvarld",
       "flags", "plan_tacker_utskottets_skal"}


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def load(name: str) -> dict[str, dict]:
    """-> {cid: decision}. out_ind is one file per case; grouped arms are JSONL."""
    d = G / name
    if not d.exists():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(d.glob("*.json")):
        try:
            out[p.stem] = json.loads(p.read_text())
        except Exception:  # noqa: BLE001 — a half-written file is not fatal
            pass
    for p in sorted(d.glob("*.jsonl")):
        for line in p.open():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if cid := r.get("cid"):
                out[str(cid)] = r  # later lines win: a restart rewrites earlier cids
    return out


def quotes(r: dict) -> list[str]:
    return [c.get("quote") or "" for c in (r.get("citations") or [])]


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    src = norm((G / "system" / "M.txt").read_text())
    gold = load("out_ind")

    arms = {}
    for raw in args:
        name, _, label = raw.partition("=")
        arms[label or name] = load(name)

    hdr = (f"{'arm':<12}{'dec':>5}{'schema':>8}{'cite/dec':>10}{'verbatim':>10}"
           f"{'med q':>8}{'explicit':>10}{'=gold':>8}{'dec-cite':>10}{'exact':>8}")
    print(f"gold = out_ind ({len(gold)} one-by-one decisions), party M\n")
    print(hdr)
    print("-" * len(hdr))

    detail = {}
    for label, got in arms.items():
        if not got:
            print(f"{label:<12}  (no decisions found)")
            continue
        n = len(got)
        bad = sum(1 for r in got.values() if not REQ <= set(r))
        tot = ok = 0
        lens: list[int] = []
        misses: list[tuple[str, str]] = []
        for cid, r in got.items():
            for q in quotes(r):
                tot += 1
                lens.append(len(q))
                if q and norm(q) in src:
                    ok += 1
                else:
                    misses.append((cid, q[:70]))
        explicit = sum(1 for r in got.values() if r.get("coverage") == "explicit")

        shared = [c for c in got if c in gold]
        same = sum(1 for c in shared if got[c].get("hallning") == gold[c].get("hallning"))
        # decisive citation: first-listed, which the prompt asks to be the one
        # that carries the vote
        dec_overlap = dec_exact = dec_n = 0
        for c in shared:
            a, b = quotes(got[c]), quotes(gold[c])
            if not a or not b:
                continue
            dec_n += 1
            qa, qb = norm(a[0]), norm(b[0])
            dec_exact += qa == qb
            dec_overlap += qa in qb or qb in qa

        med = sorted(lens)[len(lens) // 2] if lens else 0
        print(f"{label:<12}{n:>5}{bad:>8}{tot / n:>10.2f}{(ok / tot if tot else 0):>9.0%}"
              f"{med:>7}c{explicit / n:>10.0%}"
              f"{(same / len(shared) if shared else 0):>7.0%}"
              f"{(dec_overlap / dec_n if dec_n else 0):>10.0%}"
              f"{(dec_exact / dec_n if dec_n else 0):>8.0%}")
        detail[label] = (misses, got, shared)

    print("\nverbatim = quote is an exact substring of the system file the agent was served")
    print("=gold    = identical `hallning` to the one-by-one arm on shared cases")
    print("dec-cite = arm's and gold's FIRST citation overlap; `exact` = same span")

    for label, (misses, _got, _sh) in detail.items():
        if misses:
            print(f"\n{label}: {len(misses)} unverifiable quotes:")
            for cid, q in misses[:5]:
                print(f"   [{cid}] {q}")

    if len(detail) > 1:
        print("\ncoverage / confidence mix:")
        for label, (_m, got, _sh) in detail.items():
            cov = collections.Counter(r.get("coverage") for r in got.values())
            con = collections.Counter(r.get("confidence") for r in got.values())
            print(f"  {label:<12} coverage " + " ".join(f"{k}={v}" for k, v in sorted(cov.items()))
                  + "   confidence " + " ".join(f"{k}={v}" for k, v in sorted(con.items())))

        labels = list(detail)
        print("\npairwise stance agreement:")
        for i, a in enumerate(labels):
            for b in labels[i + 1:]:
                ga, gb = detail[a][1], detail[b][1]
                common = set(ga) & set(gb)
                agree = sum(1 for c in common if ga[c].get("hallning") == gb[c].get("hallning"))
                print(f"  {a} vs {b}: {agree}/{len(common)} ({agree / len(common):.0%})")


if __name__ == "__main__":
    main()
