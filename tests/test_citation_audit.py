"""The invariant that has no error path of its own.

English citations are paired with Swedish ones by *position*
(`export_site.py:149` → `CasePage.astro`'s `dEn?.citations?.[i]`), and
`repair-citations` can remove a citation. Nothing in that chain raises: the page
still renders, every quote in it is real, and the English one beside the Swedish
one is simply not its translation. So these tests are about a diff being taken at
all, and about it being taken on the right thing — shape, not text, since text is
what the passes under audit are supposed to change.

The last class runs against the committed record rather than a fixture. It is the
one that would actually have caught a misaligned ship.
"""

import json

import pytest

from aidag import citation_audit as ca


def _decision(n_citations: int, *, parti="KD", vid="V1", flags=None, blank=0, sidecar=None) -> dict:
    citations = [
        {"document": "partiprogram", "princip": f"p{i}", "quote": "" if i < blank else f"q{i}"}
        for i in range(n_citations)
    ]
    if sidecar and citations:
        citations[0][sidecar] = "vad agenten skrev"
    return {
        "parti": parti,
        "votering_id": vid,
        "prompt_version": "p6",
        "arm": "anonymous",
        "flags": list(flags or []),
        "citations": citations,
    }


def _run_dir(tmp_path, rows_by_party: dict[str, list[dict]]):
    d = tmp_path / "simulations" / "test-run"
    d.mkdir(parents=True)
    for party, rows in rows_by_party.items():
        (d / f"{party}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        )
    return tmp_path


class TestSnapshot:
    def test_it_counts_decisions_citations_and_blanks(self, tmp_path):
        root = _run_dir(
            tmp_path,
            {
                "KD": [_decision(4, vid="A"), _decision(3, vid="B", blank=1)],
                "V": [_decision(2, parti="V", vid="C")],
            },
        )
        snap = ca.snapshot("test-run", root)
        assert snap["decisions"] == 3
        assert snap["citations"] == 9
        assert snap["blank_quotes"] == 1

    def test_it_keys_lengths_on_the_cid_the_translations_use(self, tmp_path):
        root = _run_dir(tmp_path, {"KD": [_decision(4, vid="A")]})
        assert ca.snapshot("test-run", root)["lengths"] == {"KD:A:p6:anonymous": 4}

    def test_it_tallies_decision_flags_and_citation_sidecars(self, tmp_path):
        root = _run_dir(
            tmp_path,
            {
                "KD": [
                    _decision(4, vid="A", flags=["citat_svagt"], sidecar="quote_fore_migrering"),
                    _decision(4, vid="B", flags=["citat_svagt", "citat_blockerat"]),
                ]
            },
        )
        snap = ca.snapshot("test-run", root)
        assert snap["flags"] == {"citat_blockerat": 1, "citat_svagt": 2}
        assert snap["sidecars"] == {"quote_fore_migrering": 1}

    def test_it_records_no_quote_text(self, tmp_path):
        """A rewriting pass changes quotes by design; a snapshot that held them
        would report the pass working as a difference to investigate."""
        root = _run_dir(tmp_path, {"KD": [_decision(4, vid="A")]})
        assert "q0" not in json.dumps(ca.snapshot("test-run", root))

    def test_a_missing_run_is_empty_rather_than_an_error(self, tmp_path):
        snap = ca.snapshot("no-such-run", tmp_path)
        assert snap["decisions"] == 0 and snap["citations"] == 0


class TestCompare:
    def _snap(self, tmp_path, name, rows):
        root = _run_dir(tmp_path / name, {"KD": rows})
        return ca.snapshot("test-run", root)

    def test_an_untouched_run_is_aligned(self, tmp_path):
        rows = [_decision(4, vid="A"), _decision(3, vid="B")]
        before = self._snap(tmp_path, "b", rows)
        after = self._snap(tmp_path, "a", rows)
        diff = ca.compare(before, after)
        assert diff["aligned"]
        assert diff["length_changes"] == []

    def test_a_dropped_citation_is_reported_by_cid(self, tmp_path):
        """`strip_blocked` removing one citation from one decision — the exact
        event that shifts every later English quote up by one."""
        before = self._snap(tmp_path, "b", [_decision(4, vid="A"), _decision(4, vid="B")])
        after = self._snap(tmp_path, "a", [_decision(3, vid="A"), _decision(4, vid="B")])
        diff = ca.compare(before, after)
        assert not diff["aligned"]
        assert diff["length_changes"] == [{"cid": "KD:A:p6:anonymous", "before": 4, "after": 3}]
        assert diff["citations"] == {"before": 8, "after": 7}

    def test_a_decision_vanishing_from_the_run_is_reported(self, tmp_path):
        before = self._snap(tmp_path, "b", [_decision(4, vid="A"), _decision(4, vid="B")])
        after = self._snap(tmp_path, "a", [_decision(4, vid="A")])
        diff = ca.compare(before, after)
        assert not diff["aligned"]
        assert diff["dropped"] == ["KD:B:p6:anonymous"]

    def test_a_decision_appearing_is_reported(self, tmp_path):
        before = self._snap(tmp_path, "b", [_decision(4, vid="A")])
        after = self._snap(tmp_path, "a", [_decision(4, vid="A"), _decision(4, vid="B")])
        diff = ca.compare(before, after)
        assert not diff["aligned"]
        assert diff["added"] == ["KD:B:p6:anonymous"]

    def test_flag_deltas_carry_a_direction(self, tmp_path):
        """A rise in `citat_ej_verifierat` is what "investigate any increase"
        means; an unchanged flag is noise and is left out."""
        before = self._snap(
            tmp_path, "b", [_decision(4, vid="A", flags=["citat_svagt"]), _decision(4, vid="B")]
        )
        after = self._snap(
            tmp_path,
            "a",
            [
                _decision(4, vid="A", flags=["citat_svagt"]),
                _decision(4, vid="B", flags=["citat_ej_verifierat"]),
            ],
        )
        diff = ca.compare(before, after)
        assert diff["flag_deltas"] == {
            "citat_ej_verifierat": {"before": 0, "after": 1, "delta": 1}
        }

    def test_sidecar_deltas_are_reported(self, tmp_path):
        before = self._snap(tmp_path, "b", [_decision(4, vid="A")])
        after = self._snap(tmp_path, "a", [_decision(4, vid="A", sidecar="quote_ej_verifierad")])
        diff = ca.compare(before, after)
        assert diff["sidecar_deltas"]["quote_ej_verifierad"]["delta"] == 1

    def test_a_quote_rewritten_in_place_is_not_a_difference(self, tmp_path):
        """Migration and repair both rewrite quote text without moving the list.
        That must read as aligned or the check cries wolf on every run."""
        before = self._snap(tmp_path, "b", [_decision(4, vid="A")])
        root = _run_dir(tmp_path / "a", {"KD": [_decision(4, vid="A")]})
        path = root / "simulations" / "test-run" / "KD.jsonl"
        d = json.loads(path.read_text())
        d["citations"][0]["quote"] = "en helt annan text"
        path.write_text(json.dumps(d, ensure_ascii=False) + "\n")
        assert ca.compare(before, ca.snapshot("test-run", root))["aligned"]


class TestSnapshotRoundTrip:
    def test_a_snapshot_survives_the_file(self, tmp_path):
        root = _run_dir(tmp_path, {"KD": [_decision(4, vid="A", flags=["citat_svagt"])]})
        snap = ca.snapshot("test-run", root)
        path = ca.write_snapshot(snap, tmp_path / "audit" / "before.json")
        assert ca.read_snapshot(path) == snap


class TestTranslationGaps:
    """Against the committed English rather than a proxy for it."""

    @pytest.fixture
    def root(self, tmp_path, monkeypatch):
        root = _run_dir(tmp_path, {"KD": [_decision(4, vid="A"), _decision(3, vid="B")]})
        monkeypatch.setattr(ca, "RESULTS_DIR", root)
        return root

    def _english(self, root, rows: dict[str, int]):
        path = root / "translations" / "test-run"
        path.mkdir(parents=True, exist_ok=True)
        (path / "decisions.jsonl").write_text(
            "\n".join(
                json.dumps({"cid": cid, "citations": [{"quote": "x"}] * n, "motivering": "m"})
                for cid, n in rows.items()
            )
            + "\n"
        )

    def test_matching_lengths_are_no_gap(self, root):
        self._english(root, {"KD:A:p6:anonymous": 4, "KD:B:p6:anonymous": 3})
        assert ca.translation_gaps("test-run", root) == []

    def test_a_shortened_swedish_list_is_a_gap(self, root):
        self._english(root, {"KD:A:p6:anonymous": 5, "KD:B:p6:anonymous": 3})
        assert ca.translation_gaps("test-run", root) == [
            {"cid": "KD:A:p6:anonymous", "swedish": 4, "english": 5}
        ]

    def test_an_untranslated_decision_is_not_a_gap(self, root):
        """`export_site` emits `en: null` and the page falls back to Swedish —
        designed behaviour, not misalignment."""
        self._english(root, {"KD:A:p6:anonymous": 4})
        assert ca.translation_gaps("test-run", root) == []

    def test_a_translation_with_no_decision_is_a_gap(self, root):
        self._english(root, {"KD:A:p6:anonymous": 4, "KD:ZZZ:p6:anonymous": 2})
        assert ca.translation_gaps("test-run", root) == [
            {"cid": "KD:ZZZ:p6:anonymous", "swedish": None, "english": 2}
        ]


class TestWithholdingUnverifiedEnglish:
    """The other half of the pairing: same index, opposite failure.

    A length change misaligns the pair. Blanking misaligns nothing — the lists
    stay the same length — but leaves an English quote standing for a Swedish
    one that `repair-citations` withdrew precisely so it would not be shown.
    Measured on full-v4 after this task's repair: 119 of the 120 blanked
    citations had one.
    """

    def _tr(self, quotes):
        return {"citations": [{"quote": q, "princip": f"p{i}"} for i, q in enumerate(quotes)]}

    def test_english_is_kept_where_the_swedish_survives(self):
        from aidag.translate import withhold_unverified

        tr = self._tr(["the plan says", "and also"])
        out = withhold_unverified(tr, [{"quote": "planen säger"}, {"quote": "och även"}])
        assert [c["quote"] for c in out["citations"]] == ["the plan says", "and also"]

    def test_english_is_withheld_where_the_swedish_was_blanked(self):
        from aidag.translate import withhold_unverified

        tr = self._tr(["the plan says", "and also"])
        out = withhold_unverified(tr, [{"quote": ""}, {"quote": "och även"}])
        assert [c["quote"] for c in out["citations"]] == ["", "and also"]

    def test_the_princip_label_survives(self):
        """It is the model's own summary, never claimed to be verbatim — losing
        it would take the citation's only remaining label with it."""
        from aidag.translate import withhold_unverified

        out = withhold_unverified(self._tr(["the plan says"]), [{"quote": ""}])
        assert out["citations"][0]["princip"] == "p0"

    def test_an_untranslated_decision_passes_through(self):
        from aidag.translate import withhold_unverified

        assert withhold_unverified(None, [{"quote": ""}]) is None

    def test_an_unaffected_row_is_returned_unchanged(self):
        """Same object, not a copy — 20,312 decisions and 119 affected."""
        from aidag.translate import withhold_unverified

        tr = self._tr(["the plan says"])
        assert withhold_unverified(tr, [{"quote": "planen säger"}]) is tr

    def test_the_source_translation_row_is_not_mutated(self):
        from aidag.translate import withhold_unverified

        tr = self._tr(["the plan says"])
        withhold_unverified(tr, [{"quote": ""}])
        assert tr["citations"][0]["quote"] == "the plan says"


class TestTheCommandFailsOnMisalignment:
    """The report is only a report if something reads it.

    This is the invariant with no error path of its own, so `citation-audit` is
    where it has to surface: printing the affected cids and exiting 0 leaves the
    rest of the pass order — and any script driving it — treating a misaligned
    record as a clean one, which is exactly the silence the module exists for.
    """

    def _invoke(self, tmp_path, monkeypatch, sv: dict[str, int], en: dict[str, int] | None,
                baseline: dict[str, int] | None):
        from typer.testing import CliRunner

        from aidag import cli

        root = _run_dir(tmp_path, {"KD": [_decision(n, vid=vid) for vid, n in sv.items()]})
        monkeypatch.setattr(ca, "RESULTS_DIR", root)
        args = ["citation-audit", "--run-id", "test-run"]
        if baseline is not None:
            path = tmp_path / "before.json"
            ca.write_snapshot(
                {**ca.snapshot("test-run", root), "lengths": baseline}, path
            )
            args += ["--baseline", str(path)]
        if en is not None:
            d = root / "translations" / "test-run"
            d.mkdir(parents=True)
            (d / "decisions.jsonl").write_text(
                "\n".join(
                    json.dumps({"cid": cid, "citations": [{"quote": "x"}] * n})
                    for cid, n in en.items()
                )
                + "\n"
            )
            args.append("--check-translations")
        return CliRunner().invoke(cli.app, args)

    def test_an_aligned_run_exits_zero(self, tmp_path, monkeypatch):
        res = self._invoke(
            tmp_path,
            monkeypatch,
            {"A": 4},
            {"KD:A:p6:anonymous": 4},
            {"KD:A:p6:anonymous": 4},
        )
        assert res.exit_code == 0, res.output

    def test_a_length_change_against_the_baseline_exits_non_zero(self, tmp_path, monkeypatch):
        res = self._invoke(tmp_path, monkeypatch, {"A": 4}, None, {"KD:A:p6:anonymous": 5})
        assert res.exit_code == 1
        # the report still prints — the exit code is added to it, not instead of it
        assert "KD:A:p6:anonymous: 5 → 4" in res.output

    def test_an_english_pairing_gap_exits_non_zero(self, tmp_path, monkeypatch):
        res = self._invoke(tmp_path, monkeypatch, {"A": 4}, {"KD:A:p6:anonymous": 5}, None)
        assert res.exit_code == 1
        assert "sv 4 vs en 5" in res.output

    def test_a_plain_snapshot_never_fails(self, tmp_path, monkeypatch):
        """With neither flag there is nothing to be misaligned against."""
        res = self._invoke(tmp_path, monkeypatch, {"A": 4}, None, None)
        assert res.exit_code == 0, res.output


class TestARunWithNoShardsIsRefused:
    """An empty run is aligned with everything, which is the wrong kind of pass.

    `snapshot()` answering zeroes for an absent run is deliberate and pinned
    above — a snapshot of nothing is not an error. At `run()` level the same
    zeroes are a *verdict*: `compare({}, {})` is aligned and `translation_gaps`
    finds none, so a `--run-id` typo copied into both audit invocations of the
    pass order prints "English pairing still holds" and exits 0 while the real
    run went through `repair-citations` unaudited. The three sibling passes —
    `migrate_quotes.run`, `repair.run`, `anchors._load_run` — all refuse it.
    """

    def test_a_missing_run_directory_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ca, "RESULTS_DIR", tmp_path)
        with pytest.raises(FileNotFoundError, match="nothing to audit"):
            ca.run("no-such-run")

    def test_an_empty_run_directory_is_refused_too(self, tmp_path, monkeypatch):
        (tmp_path / "simulations" / "empty-run").mkdir(parents=True)
        monkeypatch.setattr(ca, "RESULTS_DIR", tmp_path)
        with pytest.raises(FileNotFoundError, match="nothing to audit"):
            ca.run("empty-run")

    def test_the_command_exits_non_zero_on_it(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from aidag import cli

        monkeypatch.setattr(ca, "RESULTS_DIR", tmp_path)
        res = CliRunner().invoke(cli.app, ["citation-audit", "--run-id", "no-such-run"])
        assert res.exit_code != 0

    def test_a_populated_run_still_audits(self, tmp_path, monkeypatch):
        root = _run_dir(tmp_path, {"KD": [_decision(4, vid="A")]})
        monkeypatch.setattr(ca, "RESULTS_DIR", root)
        assert ca.run("test-run")["snapshot"]["citations"] == 4


@pytest.fixture(scope="module")
def snap():
    from aidag.config import RESULTS_DIR

    if not (RESULTS_DIR / "simulations" / "full-v4").exists():
        pytest.skip("full-v4 not present")
    return ca.snapshot("full-v4")


class TestCommittedRecord:
    """The real run. Cheap enough to keep in the suite and the only version of
    this check that guards what actually ships."""

    def test_the_run_is_whole(self, snap):
        # 20,288, not the original 20,312. The p6 sign-inversion repair re-ran 56
        # decisions on 7 cases whose prompt gained the counter-proposal it had been
        # missing, and DELETED 24 on the 3 "Motioner som bereds förenklat" points,
        # which no single stance can answer (`casemeta.undecidable_report`).
        # 20312 - 24 = 20288. Those 24 are not coming back, so this is the whole run
        # now, not a shortfall waiting to be topped up.
        assert snap["decisions"] == 20288
        assert snap["citations"] == 77642

    def test_every_translated_decision_pairs_positionally(self, snap):
        from aidag.config import RESULTS_DIR

        if not (RESULTS_DIR / "translations" / "full-v4" / "decisions.jsonl").exists():
            pytest.skip("full-v4 decision translations not present")
        gaps = ca.translation_gaps("full-v4")
        assert gaps == [], f"{len(gaps)} decision(s) render English against the wrong Swedish"
