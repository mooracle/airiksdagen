"""Guards for the AI-vote analytics.

All three metrics are published, so their definitions are pinned here: which
parties a counterfactual is allowed to move, what a tie means, and which
decisions land in an area cell.
"""

import pytest

from aidag import aivotes


def _a(position, ja=0, nej=0, avstar=0, franvarande=0):
    return {
        "position": position,
        "n_ja": ja,
        "n_nej": nej,
        "n_avstar": avstar,
        "n_franvarande": franvarande,
    }


def _d(rost, tier="explicit"):
    return {"rost": rost, "tier": tier}


# ── flip ────────────────────────────────────────────────────────────────────


def test_no_qualifying_party_yields_no_counterfactual():
    # the plan agrees with the vote, so there is nothing to re-run
    assert aivotes.flip({"M": _a("Ja", ja=50)}, {"M": _d("Ja")}) is None


def test_explicit_divergence_moves_the_whole_cast_contingent():
    actual = {
        "M": _a("Ja", ja=48, avstar=2, franvarande=5),
        "S": _a("Nej", nej=45),
    }
    out = aivotes.flip(actual, {"M": _d("Nej"), "S": _d("Nej")})
    assert out["parties"] == ["M"]
    assert out["actual"] == {"ja": 48, "nej": 45, "outcome": "Ja"}
    # both M's Ja votes and its abstainers go to Nej; absentees stay absent
    assert out["counterfactual"] == {"ja": 0, "nej": 95, "outcome": "Nej"}
    assert out["flips"] is True


def test_weaker_tiers_never_move_a_vote():
    actual = {"M": _a("Ja", ja=48), "S": _a("Nej", nej=45)}
    for tier in ("extrapolated", "off_axis", None):
        assert aivotes.flip(actual, {"M": _d("Nej", tier)}) is None


def test_abstaining_party_is_left_where_it_stood():
    # an abstention is a floor tactic the plan was never asked to express: MP is
    # not moved onto the axis, so no counterfactual exists at all here
    actual = {"M": _a("Ja", ja=48), "MP": _a("Avstår", avstar=15)}
    assert aivotes.flip(actual, {"MP": _d("Nej")}) is None


def test_a_tie_is_not_a_flip():
    actual = {"M": _a("Ja", ja=50), "S": _a("Nej", nej=40)}
    out = aivotes.flip(actual, {"M": _d("Nej")})
    # 50 Ja -> 0, so 0 vs 90: this one does flip
    assert out["flips"] is True
    # engineer an exact tie instead: M moves 45 of the 90 cast
    actual = {"M": _a("Ja", ja=45), "S": _a("Nej", nej=45), "V": _a("Ja", ja=45)}
    out = aivotes.flip(actual, {"M": _d("Nej")})
    assert out["counterfactual"] == {"ja": 45, "nej": 90, "outcome": "Nej"}
    actual = {"M": _a("Ja", ja=45), "S": _a("Nej", nej=45)}
    out = aivotes.flip(actual, {"M": _d("Nej"), "S": _d("Ja")})
    assert out["counterfactual"]["outcome"] is None
    assert out["flips"] is False


def test_unmoved_parties_keep_their_own_dissenters():
    # S votes Nej as a line but 3 members broke ranks; the counterfactual must
    # carry those 3 Ja votes rather than the party line's seat total
    actual = {"M": _a("Ja", ja=40), "S": _a("Nej", ja=3, nej=42)}
    out = aivotes.flip(actual, {"M": _d("Nej")})
    assert out["counterfactual"] == {"ja": 3, "nej": 82, "outcome": "Nej"}


# ── flip_summary ────────────────────────────────────────────────────────────


def _case(vid, area, actual, ai, datum="2024-01-01"):
    case = {
        "votering_id": vid,
        "datum": datum,
        "rubrik": vid,
        "utskott": "FiU",
        "policy_area": area,
        "actual": actual,
        "ai": ai,
    }
    case["flip"] = aivotes.flip(actual, ai)
    return case


def test_flip_summary_counts_only_divisions_that_changed():
    cases = [
        # flips Ja -> Nej, and M alone was enough
        _case("a", "tax", {"M": _a("Ja", ja=50), "S": _a("Nej", nej=40)}, {"M": _d("Nej")}),
        # M diverges but the chamber's answer survives
        _case("b", "tax", {"M": _a("Ja", ja=10), "S": _a("Ja", ja=90)}, {"M": _d("Nej")}),
        # no explicit divergence at all
        _case("c", "culture", {"M": _a("Ja", ja=50)}, {"M": _d("Ja")}),
    ]
    s = aivotes.flip_summary(cases)
    assert s["n_cases"] == 3
    assert s["n_with_movers"] == 2
    assert s["n_flipped"] == 1
    assert s["n_solo"] == 1
    assert s["direction"] == {"ja_to_nej": 1, "nej_to_ja": 0}
    assert s["per_party"]["M"] == {"movers": 2, "flips": 1, "solo": 1}
    assert "S" not in s["per_party"]
    assert [c["votering_id"] for c in s["cases"]] == ["a"]
    # every division counts toward its area's denominator, flipped or not
    assert s["per_area"]["tax"] == {
        "n_cases": 2, "with_movers": 2, "flipped": 1, "flip_rate": 0.5,
    }
    assert s["per_area"]["culture"]["flip_rate"] == 0.0


def test_flip_summary_marks_multi_party_flips_as_not_solo():
    cases = [
        _case(
            "a", "tax",
            {"M": _a("Ja", ja=30), "KD": _a("Ja", ja=30), "S": _a("Nej", nej=50)},
            {"M": _d("Nej"), "KD": _d("Nej")},
        ),
    ]
    s = aivotes.flip_summary(cases)
    assert s["n_flipped"] == 1
    assert s["n_solo"] == 0
    assert s["per_party"]["M"]["flips"] == s["per_party"]["KD"]["flips"] == 1
    assert s["per_party"]["M"]["solo"] == 0


def test_flip_summary_reports_ties_separately_from_flips():
    cases = [_case("a", "tax", {"M": _a("Ja", ja=45), "S": _a("Nej", nej=45)},
                   {"M": _d("Nej"), "S": _d("Ja")})]
    s = aivotes.flip_summary(cases)
    assert s["n_indeterminate"] == 1
    assert s["n_flipped"] == 0


def test_flip_summary_orders_cases_newest_first():
    cases = [
        _case("old", "tax", {"M": _a("Ja", ja=50), "S": _a("Nej", nej=40)},
              {"M": _d("Nej")}, datum="2023-01-01"),
        _case("new", "tax", {"M": _a("Ja", ja=50), "S": _a("Nej", nej=40)},
              {"M": _d("Nej")}, datum="2025-01-01"),
    ]
    assert [c["votering_id"] for c in aivotes.flip_summary(cases)["cases"]] == ["new", "old"]


# ── pairs_matrix ────────────────────────────────────────────────────────────


def test_pairs_matrix_counts_matching_plan_votes():
    cases = [
        {"rm": "2023/24", "ai": {"M": "Ja", "KD": "Ja", "V": "Nej"}},
        {"rm": "2023/24", "ai": {"M": "Ja", "KD": "Nej", "V": "Nej"}},
    ]
    m = aivotes.pairs_matrix(cases)
    assert m["overall"]["M"]["KD"] == 0.5
    assert m["overall"]["M"]["V"] == 0.0
    assert m["overall"]["V"]["KD"] == 0.5
    assert m["overall"]["M"]["M"] == 1.0
    # a party with no decisions has no denominator anywhere
    assert m["overall"]["S"]["M"] is None


def test_pairs_matrix_is_symmetric_and_split_per_riksmote():
    cases = [
        {"rm": "2022/23", "ai": {"M": "Ja", "S": "Ja"}},
        {"rm": "2023/24", "ai": {"M": "Ja", "S": "Nej"}},
    ]
    m = aivotes.pairs_matrix(cases)
    assert m["overall"]["M"]["S"] == m["overall"]["S"]["M"] == 0.5
    assert m["per_rm"]["2022/23"]["M"]["S"] == 1.0
    assert m["per_rm"]["2023/24"]["M"]["S"] == 0.0


# ── area_stats ──────────────────────────────────────────────────────────────


def test_area_stats_splits_follow_rate_from_the_explicit_gap():
    cases = [
        {
            "policy_area": "tax",
            "actual": {"M": _a("Ja"), "S": _a("Nej")},
            "ai": {"M": _d("Ja"), "S": _d("Ja", "off_axis")},
        },
        {
            "policy_area": "tax",
            "actual": {"M": _a("Ja"), "S": _a("Nej")},
            "ai": {"M": _d("Nej"), "S": _d("Nej")},
        },
    ]
    out = aivotes.area_stats(cases)
    tax = out["per_area"]["tax"]
    assert tax["n_cases"] == 2
    # M followed 1 of 2, and both its decisions were explicit -> 1 documented gap
    assert tax["per_party"]["M"]["follow_rate"] == 0.5
    assert tax["per_party"]["M"]["explicit_n"] == 2
    assert tax["per_party"]["M"]["explicit_gap"] == 1
    # S followed 1 of 2 too, but only one decision reached the explicit tier
    assert tax["per_party"]["S"]["follow_rate"] == 0.5
    assert tax["per_party"]["S"]["explicit_n"] == 1
    assert tax["per_party"]["S"]["explicit_gap"] == 0
    assert tax["all_parties"]["n"] == 4
    assert out["overall"]["follow_rate"] == 0.5


def test_area_stats_skips_absences_and_unlabelled_cases():
    cases = [
        {
            "policy_area": None,  # metadata not generated: not a policy area
            "actual": {"M": _a("Ja")},
            "ai": {"M": _d("Ja")},
        },
        {
            "policy_area": "culture",
            "actual": {"M": _a("Frånvarande"), "S": _a("Ja")},
            "ai": {"M": _d("Ja"), "S": _d("Ja")},
        },
    ]
    out = aivotes.area_stats(cases)
    assert list(out["per_area"]) == ["culture"]
    assert "M" not in out["per_area"]["culture"]["per_party"]
    assert out["per_party"]["S"]["n"] == 1
    assert "M" not in out["per_party"]


def test_abstentions_sit_beside_the_denominator_not_inside_it():
    # a p6 stance only ever derives to Ja or Nej, so scoring an abstention as a
    # miss would make abstention-heavy areas look like plan-defying ones
    cases = [
        {
            "policy_area": "civil",
            "actual": {"M": _a("Avstår"), "S": _a("Ja")},
            "ai": {"M": _d("Ja"), "S": _d("Ja")},
        },
    ]
    out = aivotes.area_stats(cases)
    m = out["per_area"]["civil"]["per_party"]["M"]
    assert m["n"] == 0 and m["abstained"] == 1 and m["follow_rate"] is None
    assert m["explicit_n"] == 0
    assert out["per_area"]["civil"]["all_parties"]["follow_rate"] == 1.0
    assert out["per_area"]["civil"]["all_parties"]["abstained"] == 1


def test_area_stats_orders_areas_by_case_count():
    cases = [{"policy_area": "tax", "actual": {}, "ai": {}} for _ in range(3)]
    cases += [{"policy_area": "culture", "actual": {}, "ai": {}} for _ in range(5)]
    assert aivotes.area_stats(cases)["areas"] == ["culture", "tax"]


@pytest.mark.parametrize("empty", [[], [{"policy_area": "tax", "actual": {}, "ai": {}}]])
def test_area_stats_survives_an_empty_corpus(empty):
    out = aivotes.area_stats(empty)
    assert out["overall"]["follow_rate"] is None
