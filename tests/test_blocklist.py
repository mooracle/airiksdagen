"""Pins the generic-citation block list: it must catch every window-variant of
the boilerplate sentences, leave real policy quotes alone, and keep the vote."""

from aidag.blocklist import is_blocked, is_weak, mark_weak, strip_blocked


class TestIsBlocked:
    def test_coalition_boilerplate_variants_all_blocked(self):
        # different sub-windows of the same Tidöavtalet sentences agents quote
        for q in [
            "är överens om att ta ansvar för Sverige i ett gemensamt samarbete under mandatperioden",
            "ta ansvar för Sverige i ett gemensamt samarbete",
            "de ingående partierna inte ensidigt samverkar med andra partier i",
            "Samarbetet innebär också att de ingående partierna inte ensidigt samverkar med andra",
            "rösta på regeringens budget så att budgeten i sin helhet röstas igenom i riksdagen.",
            "Samarbetsparti utanför regeringen är Sverigedemokraterna.",
        ]:
            assert is_blocked("tidoavtalet", q), q

    def test_party_identity_lines_blocked(self):
        assert is_blocked("partiprogram", "Moderaternas syn på politiskt arbete är därför anti-radikal")
        assert is_blocked(
            "valmanifest",
            "Vänsterpartiet är ett socialistiskt, feministiskt och antirasistiskt parti på ekologisk grund.",
        )
        assert is_blocked(
            "partiprogram",
            "Sverige ska ha en välfungerande marknadsekonomi, fri från planekonomiska pekpinnar.",
        )

    def test_real_policy_quotes_are_kept(self):
        # frequent, but concretely about the case — must NOT be blocked
        for doc, q in [
            ("valmanifest", "Ta bort en tredjedel av företagens administrationskostnader och en tredjedel av regelkrånglet"),
            ("tidoavtalet", "Ett högkostnadsskydd till drabbade hushåll och företag så snart det är praktiskt möjligt"),
            ("valmanifest", "Krafttag för att stoppa hedersrelaterat våld, sexualbrott och mäns våld mot kvinnor."),
            ("tidoavtalet", "Straffskalorna, påföljderna och systemet för straffmätning ska ses över."),
        ]:
            assert not is_blocked(doc, q), q

    def test_document_scoped(self):
        # the phrase only counts against the document it actually lives in
        assert not is_blocked("valmanifest", "ta ansvar för Sverige i ett gemensamt samarbete")

    def test_whitespace_and_empty(self):
        assert is_blocked("tidoavtalet", "ta ansvar  för Sverige\ni ett gemensamt   samarbete")
        assert not is_blocked("tidoavtalet", "")


class TestStripBlocked:
    def test_keeps_vote_drops_generic_citation_and_flags(self):
        d = {
            "rost": "Ja",
            "citations": [
                {"document": "tidoavtalet", "quote": "rösta på regeringens budget så att budgeten i sin helhet röstas igenom i riksdagen.", "princip": "budgetdisciplin"},
                {"document": "valmanifest", "quote": "Krafttag för att stoppa hedersrelaterat våld", "princip": "brott"},
            ],
            "flags": [],
        }
        assert strip_blocked(d) == 1
        assert d["rost"] == "Ja"
        assert [c["document"] for c in d["citations"]] == ["valmanifest"]
        assert "citat_blockerat" in d["flags"]

    def test_all_generic_leaves_zero_citations(self):
        d = {
            "rost": "Ja",
            "citations": [
                {"document": "tidoavtalet", "quote": "ta ansvar för Sverige i ett gemensamt samarbete", "princip": "samarbete"},
            ],
            "flags": [],
        }
        assert strip_blocked(d) == 1
        assert d["citations"] == []
        assert "citat_blockerat" in d["flags"]

    def test_noop_when_nothing_generic(self):
        d = {
            "citations": [{"document": "valmanifest", "quote": "Krafttag mot brott", "princip": "x"}],
            "flags": [],
        }
        assert strip_blocked(d) == 0
        assert d["flags"] == []


class TestWeak:
    def test_weak_phrases_detected(self):
        assert is_weak("partiprogram", "Svensk utrikespolitik ska värna svenska intressen.")
        assert is_weak("partiprogram", "Alla människor har rätt till en bred privat sfär")
        assert is_weak(
            "partiprogram",
            "All lagstiftning bör prövas mot dess påverkan på människors personliga integritet.",
        )

    def test_weak_and_blocked_are_disjoint(self):
        # a coalition/identity line is blocked, never merely weak
        assert is_blocked("tidoavtalet", "ta ansvar för Sverige i ett gemensamt samarbete")
        assert not is_weak("tidoavtalet", "ta ansvar för Sverige i ett gemensamt samarbete")
        # a concrete policy quote is neither
        assert not is_weak("valmanifest", "Krafttag för att stoppa hedersrelaterat våld")

    def test_mark_weak_annotates_and_flags(self):
        d = {
            "citations": [
                {"document": "partiprogram", "quote": "Svensk utrikespolitik ska värna svenska intressen.", "princip": "utrikes"},
                {"document": "valmanifest", "quote": "Krafttag mot brott", "princip": "brott"},
            ],
            "flags": [],
        }
        assert mark_weak(d) == 1
        assert d["citations"][0].get("svag") is True
        assert "svag" not in d["citations"][1]
        assert "citat_svagt" in d["flags"]
        # keeps the vote/citation — nothing removed
        assert len(d["citations"]) == 2

    def test_mark_weak_reconciles_stale_markers(self):
        # a citation wrongly marked weak is cleared and the flag removed
        d = {
            "citations": [{"document": "valmanifest", "quote": "Krafttag mot brott", "princip": "x", "svag": True}],
            "flags": ["citat_svagt"],
        }
        assert mark_weak(d) == 0
        assert "svag" not in d["citations"][0]
        assert "citat_svagt" not in d["flags"]
