"""Audit the citation record across a pass that rewrites it.

`repair-citations` does not only edit quotes — `blocklist.strip_blocked()`
(`repair.py:134`) **removes** citations. The site pairs English translations with
Swedish citations **positionally**: `export_site.py:149` hands the translation
row through untouched and `CasePage.astro` reads `dEn?.citations?.[i]`. So a pass
that shortens one decision's citation list does not raise anything. It renders
the wrong English quote beside the wrong Swedish one, on a page whose entire
claim is that the quote is verbatim.

That failure is silent in every direction — no exception, no schema violation, no
count that looks wrong at the run level, because the citations that remain are
all real. The only way to see it is to hold the shape of the record from before
the pass and diff it afterwards, per decision. That is what this module is:

    snapshot()     the shape — per-decision citation counts, decision flags,
                   sidecar and blank-quote tallies. Not the text; the text is
                   what the pass is *supposed* to change.
    compare()      before vs after, with every length change listed by cid so
                   the affected decisions can be re-translated rather than
                   shipped misaligned.
    translation_gaps()
                   the same invariant against the committed English, which is
                   where the misalignment would actually surface.

`compare()` reports rather than asserts, and the caller decides. A flag count
that *rises* is the interesting direction (repair found more unverifiable quotes
than the baseline had) but it is not automatically wrong; a length change is.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from aidag.config import RESULTS_DIR

# Sidecar fields, each written by a different pass and each recording what the
# quote used to say. Counting them is how a pass's own footprint stays visible
# after it has run: `quote_ej_verifierad` is repair's, `quote_fore_migrering` is
# the corpus migration's.
SIDECARS = ("quote_ej_verifierad", "quote_fore_migrering")


def cid_of(d: dict) -> str:
    """The decision id the site, the translations and the run all key on."""
    return f"{d['parti']}:{d['votering_id']}:{d['prompt_version']}:{d['arm']}"


def _read_jsonl(path: Path):
    for line in path.read_text().splitlines():
        if line.strip():
            yield json.loads(line)


def snapshot(run_id: str, results_dir: Path | None = None) -> dict:
    """The shape of a run's citation record, keyed by cid.

    Deliberately excludes quote *text*: a rewriting pass is expected to change
    that, and a snapshot that flagged it would report the pass working as a
    difference to investigate.
    """
    root = (results_dir or RESULTS_DIR) / "simulations" / run_id
    lengths: dict[str, int] = {}
    flags: Counter = Counter()
    sidecars: Counter = Counter()
    n_blank = 0
    for path in sorted(root.glob("*.jsonl")):
        for d in _read_jsonl(path):
            citations = d.get("citations") or []
            lengths[cid_of(d)] = len(citations)
            for flag in d.get("flags") or []:
                flags[flag] += 1
            for c in citations:
                if not c.get("quote"):
                    n_blank += 1
                for field in SIDECARS:
                    if field in c:
                        sidecars[field] += 1
    return {
        "run_id": run_id,
        "decisions": len(lengths),
        "citations": sum(lengths.values()),
        "blank_quotes": n_blank,
        "flags": dict(sorted(flags.items())),
        "sidecars": dict(sorted(sidecars.items())),
        "lengths": lengths,
    }


def write_snapshot(snap: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False))
    return path


def read_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def compare(before: dict, after: dict) -> dict:
    """Diff two snapshots.

    `length_changes` is the one that matters: those cids' English translations
    are now paired against the wrong Swedish quotes and have to be re-issued.
    `dropped` and `added` cover a decision leaving or entering the run entirely,
    which no pass here is allowed to do but which a truncated write would cause.
    """
    b, a = before["lengths"], after["lengths"]
    changes = [
        {"cid": cid, "before": b[cid], "after": a[cid]} for cid in sorted(b) if cid in a and b[cid] != a[cid]
    ]
    flag_deltas = {
        flag: {
            "before": before["flags"].get(flag, 0),
            "after": after["flags"].get(flag, 0),
            "delta": after["flags"].get(flag, 0) - before["flags"].get(flag, 0),
        }
        for flag in sorted(set(before["flags"]) | set(after["flags"]))
        if before["flags"].get(flag, 0) != after["flags"].get(flag, 0)
    }
    return {
        "aligned": not changes and not (set(b) - set(a)) and not (set(a) - set(b)),
        "length_changes": changes,
        "dropped": sorted(set(b) - set(a)),
        "added": sorted(set(a) - set(b)),
        "decisions": {"before": before["decisions"], "after": after["decisions"]},
        "citations": {"before": before["citations"], "after": after["citations"]},
        "blank_quotes": {"before": before["blank_quotes"], "after": after["blank_quotes"]},
        "flag_deltas": flag_deltas,
        "sidecar_deltas": {
            field: {
                "before": before["sidecars"].get(field, 0),
                "after": after["sidecars"].get(field, 0),
                "delta": after["sidecars"].get(field, 0) - before["sidecars"].get(field, 0),
            }
            for field in SIDECARS
            if before["sidecars"].get(field, 0) != after["sidecars"].get(field, 0)
        },
    }


def translation_gaps(run_id: str, results_dir: Path | None = None) -> list[dict]:
    """Cids whose committed English citation list is a different length.

    This is the misalignment itself rather than a proxy for it. A cid with no
    translation row is not a gap — `export_site` emits `en: null` and the page
    falls back to Swedish, which is the designed behaviour.

    The path is derived from `results_dir` rather than taken from
    `translate.decisions_path`, so overriding the root moves the Swedish and the
    English together. Reading one side from a fixture and the other from the
    committed run would compare nothing and pass.
    """
    root = results_dir or RESULTS_DIR
    path = root / "translations" / run_id / "decisions.jsonl"
    swedish = snapshot(run_id, results_dir)["lengths"]
    english = {r["cid"]: r for r in (_read_jsonl(path) if path.exists() else [])}
    gaps = []
    for cid, row in sorted(english.items()):
        n_en = len(row.get("citations") or [])
        if cid not in swedish:
            gaps.append({"cid": cid, "swedish": None, "english": n_en})
        elif n_en != swedish[cid]:
            gaps.append({"cid": cid, "swedish": swedish[cid], "english": n_en})
    return gaps


def _print_compare(diff: dict) -> None:
    print(
        f"decisions {diff['decisions']['before']} → {diff['decisions']['after']}, "
        f"citations {diff['citations']['before']} → {diff['citations']['after']}, "
        f"blank quotes {diff['blank_quotes']['before']} → {diff['blank_quotes']['after']}"
    )
    if diff["aligned"]:
        print("citation lists: unchanged per decision — English pairing still holds")
    else:
        n = len(diff["length_changes"])
        print(f"citation lists: {n} decision(s) CHANGED LENGTH — re-translate these cids:")
        for row in diff["length_changes"][:40]:
            print(f"  {row['cid']}: {row['before']} → {row['after']}")
        if n > 40:
            print(f"  ... and {n - 40} more")
        for label, key in (("dropped", "dropped"), ("added", "added")):
            if diff[key]:
                print(f"  {len(diff[key])} decision(s) {label} from the run: {diff[key][:5]}")
    for flag, row in diff["flag_deltas"].items():
        print(f"  flag {flag}: {row['before']} → {row['after']} ({row['delta']:+d})")
    for field, row in diff["sidecar_deltas"].items():
        print(f"  sidecar {field}: {row['before']} → {row['after']} ({row['delta']:+d})")


def run(
    run_id: str,
    out: str | None = None,
    baseline: str | None = None,
    check_translations: bool = False,
) -> dict:
    """Snapshot a run; against `--baseline`, diff it and report.

    Returns the diff and the gaps as well as the snapshot: this is the one
    invariant with no error path of its own, so the caller has to be able to fail
    on it rather than read the printed report (`cli.citation_audit` exits
    non-zero). `compare()` still only reports — the decision lives here.

    A `--run-id` that globs no shards is refused here, the same way
    `migrate_quotes.run()`, `repair.run()` and `anchors._load_run()` refuse it.
    `snapshot()` deliberately answers an absent run with zeroes (a snapshot of
    nothing is not an error), but at *this* level the zeroes read as a verdict:
    an empty run diffs and pairs cleanly against anything, so a typo copied into
    both audit invocations of the pass order prints "unchanged per decision —
    English pairing still holds", finds 0 gaps and exits 0, while the real run
    went through `repair-citations` unaudited.
    """
    root = RESULTS_DIR / "simulations" / run_id
    if not sorted(root.glob("*.jsonl")):
        raise FileNotFoundError(
            f"{run_id}: no *.jsonl under {root} — nothing to audit. An empty run "
            "is aligned with everything, so this would pass the one check that "
            "has no other error path."
        )
    snap = snapshot(run_id)
    print(
        f"{run_id}: {snap['decisions']} decisions, {snap['citations']} citations, "
        f"{snap['blank_quotes']} blank"
    )
    for flag, n in snap["flags"].items():
        print(f"  {flag}: {n}")
    for field, n in snap["sidecars"].items():
        print(f"  {field}: {n}")
    diff = None
    gaps: list[dict] | None = None
    if baseline:
        diff = compare(read_snapshot(Path(baseline)), snap)
        print(f"\nvs {baseline}:")
        _print_compare(diff)
    if check_translations:
        gaps = translation_gaps(run_id)
        print(
            f"\nEnglish pairing: {len(gaps)} translated decision(s) whose citation "
            "list length differs from the Swedish"
        )
        for row in gaps[:20]:
            print(f"  {row['cid']}: sv {row['swedish']} vs en {row['english']}")
        if len(gaps) > 20:
            print(f"  ... and {len(gaps) - 20} more")
    if out:
        print(f"\nwrote {write_snapshot(snap, Path(out))}")
    return {"snapshot": snap, "diff": diff, "translation_gaps": gaps}
