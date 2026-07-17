"""Generic-citation block list — quotes that don't explain a specific vote.

Some documents contain lines that a party could cite to justify almost ANY
vote: the Tidöavtalet's coalition-mechanics clauses ("vote for the government's
budget so it passes in its entirety", "take responsibility for Sweden in a
joint cooperation") and each party's self-identity / mission statements
("Vänsterpartiet är ett socialistiskt … parti"). When an agent leans on one of
these, the citation adds nothing to *why this party voted this way on this
case* — the vote is really coming from the party's overall platform, not from
that line. Worse, the coalition-mechanics quotes leak coalition discipline into
the programme-citation channel that the coalition-vs-programme metric exists to
keep separate.

So we drop these citations and keep the decision (vote + motivering) as-is: an
honest "here is the vote, with no specific supporting quote" beats a filler
quote. `repair.run()` applies this after aligning quotes to their verbatim
span, so it strips every window-variant of a boilerplate sentence and — because
`repair-citations` runs after every ingest — it applies to all committed
decisions and to every future batch (the "don't cite them again" half).

Matching is deliberately loose: a citation is blocked when its (whitespace- and
case-normalized) quote either contains, or is contained by, a canonical phrase
below — that catches the many different sub-windows agents quote of the same
sentence. Each phrase is long and distinctive enough that either-direction
containment cannot catch a genuine policy quote (pinned in test_blocklist.py).
Adding an entry is a one-line change; keep phrases specific and say why.
"""

from __future__ import annotations

import re
import unicodedata

# Each entry: the document the phrase lives in, a canonical (longest useful)
# core of the boilerplate sentence, why it's generic, and a category tag.
BLOCKLIST: list[dict[str, str]] = [
    # -- coalition / procedural boilerplate (Tidöavtalet) -------------------
    # Justify a vote by the mechanics of governing together, never by policy.
    {
        "document": "tidoavtalet",
        "phrase": "ta ansvar för Sverige i ett gemensamt samarbete",
        "category": "coalition",
        "reason": "coalition-formation boilerplate; says nothing about the case",
    },
    {
        "document": "tidoavtalet",
        "phrase": "ensidigt samverkar med andra",
        "category": "coalition",
        "reason": "coalition-discipline (no unilateral cooperation); not policy",
    },
    {
        "document": "tidoavtalet",
        "phrase": "rösta på regeringens budget så att budgeten i sin helhet röstas igenom",
        "category": "coalition",
        "reason": "budget-discipline mechanics; leaks coalition into programme channel",
    },
    {
        "document": "tidoavtalet",
        "phrase": "Samarbetsparti utanför regeringen är Sverigedemokraterna",
        "category": "coalition",
        "reason": "describes SD's structural role; carries no policy content",
    },
    # -- party self-identity / mission statements --------------------------
    # A party's blanket description of itself — applies to every vote equally.
    {
        "document": "partiprogram",
        "phrase": "Moderaternas syn på politiskt arbete är därför anti-radikal",
        "category": "identity",
        "reason": "M self-characterisation; generic across all cases",
    },
    {
        "document": "valmanifest",
        "phrase": "Vänsterpartiet är ett socialistiskt, feministiskt och antirasistiskt parti",
        "category": "identity",
        "reason": "V self-definition; generic across all cases",
    },
    {
        "document": "partiprogram",
        "phrase": "välfungerande marknadsekonomi, fri från planekonomiska pekpinnar",
        "category": "identity",
        "reason": "C general economic disposition; generic across all cases",
    },
    {
        "document": "partiprogram",
        "phrase": "politiska reformer lutar sig mot forskning och beprövad erfarenhet",
        "category": "identity",
        "reason": "M meta-principle (evidence-based); justifies any vote",
    },
]

# Weak tier: broad principles / dispositions that DO edge toward policy, so we
# keep them but mark them `svag` — a supporting-but-generic citation, not the
# decisive one. These name a value applied across many cases (foreign-policy
# disposition, evidence-based climate policy, personal-integrity principles)
# rather than a concrete commitment on the case at hand. Same match rule as the
# block list; kept in the record and rendered with a "weak support" marker.
WEAK_LIST: list[dict[str, str]] = [
    {
        "document": "partiprogram",
        "phrase": "Svensk utrikespolitik ska värna svenska intressen",
        "category": "philosophy",
        "reason": "broad foreign-policy disposition, not a concrete position",
    },
    {
        "document": "partiprogram",
        "phrase": "Vetenskap och fakta ska vara ledande",
        "category": "philosophy",
        "reason": "evidence-based meta-principle applied across climate cases",
    },
    {
        "document": "partiprogram",
        "phrase": "Alla människor har rätt till en bred privat sfär",
        "category": "philosophy",
        "reason": "personal-integrity principle, applied broadly",
    },
    {
        "document": "partiprogram",
        "phrase": "All lagstiftning bör prövas mot dess påverkan på människors personliga integritet",
        "category": "philosophy",
        "reason": "personal-integrity meta-principle, applied broadly",
    },
]


def _key(text: str) -> str:
    """NFC-normalize, collapse whitespace, casefold — the matching form."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold()


# Precomputed once: (document, normalized phrase).
_ENTRIES: list[tuple[str, str]] = [(e["document"], _key(e["phrase"])) for e in BLOCKLIST]
_WEAK_ENTRIES: list[tuple[str, str]] = [(e["document"], _key(e["phrase"])) for e in WEAK_LIST]


def _matches(document: str, quote: str, entries: list[tuple[str, str]]) -> bool:
    if not quote:
        return False
    q = _key(quote)
    return any(document == doc and (phrase in q or q in phrase) for doc, phrase in entries)


def is_blocked(document: str, quote: str) -> bool:
    """True if this citation quotes a block-listed generic phrase.

    Loose match: same document, and the normalized quote contains — or is a
    sub-window of — a canonical phrase. Both directions because agents quote
    different slices of the same boilerplate sentence.
    """
    return _matches(document, quote, _ENTRIES)


def is_weak(document: str, quote: str) -> bool:
    """True if this citation quotes a weak-tier broad-principle phrase."""
    return _matches(document, quote, _WEAK_ENTRIES)


def strip_blocked(decision: dict) -> int:
    """Remove block-listed citations from `decision` in place.

    Keeps the vote and motivering untouched (an unsupported-but-honest
    decision), flags `citat_blockerat` when anything was removed, and returns
    the count dropped.
    """
    cites = decision.get("citations") or []
    kept = [c for c in cites if not is_blocked(c.get("document", ""), c.get("quote", ""))]
    removed = len(cites) - len(kept)
    if removed:
        decision["citations"] = kept
        flags = decision.setdefault("flags", [])
        if "citat_blockerat" not in flags:
            flags.append("citat_blockerat")
    return removed


def mark_weak(decision: dict) -> int:
    """Annotate weak-tier citations with `svag: true` (else clear the marker).

    Pure annotation — nothing is removed, so the vote and the citation stay.
    Fully reconciles each run (sets/clears `svag` per citation and the decision
    `citat_svagt` flag) so editing WEAK_LIST + re-running repair is idempotent.
    Returns the number of weak citations.
    """
    n_weak = 0
    for c in decision.get("citations") or []:
        if is_weak(c.get("document", ""), c.get("quote", "")):
            c["svag"] = True
            n_weak += 1
        else:
            c.pop("svag", None)
    flags = decision.setdefault("flags", [])
    if n_weak and "citat_svagt" not in flags:
        flags.append("citat_svagt")
    elif not n_weak and "citat_svagt" in flags:
        flags.remove("citat_svagt")
    return n_weak
