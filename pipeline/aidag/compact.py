"""Compact, human-readable meaning of Ja/Nej/Avstår for one votering.

The committee proposal text is legal boilerplate ("Riksdagen antar regeringens
förslag till lag om ändring i lagen (2022:1832) om ..."). This module reduces
it to what a Ja vote actually does, rule-based on the dominant phrasings, in
Swedish and English. Deterministic on purpose: no model involved, so the
explainer can never contradict the source text it sits next to.
"""

from __future__ import annotations

import re

# (pattern, sv, en) — first matches win; {x} is the captured topic if any
RULES: list[tuple[str, str, str]] = [
    (
        r"ställer sig bakom det som utskottet anför om (.+?) och tillkännager",
        "ett tillkännagivande som uppmanar regeringen att agera om {x}",
        "a formal request (tillkännagivande) telling the government to act on {x}",
    ),
    (
        r"antar regeringens förslag",
        "att anta regeringens lagförslag",
        "adopting the government's legislative proposal",
    ),
    (
        r"anvisar anslagen",
        "att fördela budgetanslagen enligt utskottets förslag",
        "allocating the budget funds as the committee proposes",
    ),
    (
        r"godkänner regeringens förslag",
        "att godkänna regeringens förslag",
        "approving the government's proposal",
    ),
    (
        r"godkänner",
        "att godkänna det som utskottet föreslår",
        "approving what the committee proposes",
    ),
    (
        r"lägger (regeringens\s+)?skrivelse[^.]*till handlingarna",
        "att lägga regeringens skrivelse till handlingarna (ärendet avslutas utan åtgärd)",
        "closing the file on the government's report (no further action)",
    ),
    (
        r"avslår motion",
        "att avslå motionsyrkandena (nuvarande ordning behålls)",
        "rejecting the motions (keeping things as they are)",
    ),
    (
        r"bifaller motion",
        "att bifalla motionsyrkanden",
        "approving motion proposals",
    ),
]

MAX_TOPIC = 120


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _ja_actions(forslag: str) -> tuple[list[str], list[str]]:
    sv_parts: list[str] = []
    en_parts: list[str] = []
    for pattern, sv, en in RULES:
        m = re.search(pattern, forslag, flags=re.IGNORECASE)
        if not m:
            continue
        x = _shorten(m.group(1), MAX_TOPIC) if m.groups() and m.group(1) else ""
        sv_parts.append(sv.format(x=x))
        en_parts.append(en.format(x=x))
        if len(sv_parts) == 2:  # at most two actions keeps it compact
            break
    if not sv_parts:
        fallback = _shorten(forslag.split(".")[0], 140)
        sv_parts = [fallback]
        en_parts = [fallback]
    return sv_parts, en_parts


def compact_meanings(forslag_text: str, alternatives: list[dict]) -> dict:
    """-> {ja_sv, nej_sv, avstar_sv, ja_en, nej_en, avstar_en}."""
    sv_actions, en_actions = _ja_actions(forslag_text or "")
    ja_sv = "Utskottets förslag: " + " samt ".join(sv_actions) + "."
    ja_en = "The committee proposal: " + " and ".join(en_actions) + "."

    reservations = [a for a in alternatives if a.get("alt_id", "").startswith("res-")]
    if reservations:
        res = reservations[0]
        n = res["alt_id"].removeprefix("res-")
        partier = ", ".join(res.get("source_partier", []))
        who_sv = f" från {partier}" if partier else ""
        who_en = f" by {partier}" if partier else ""
        label = res.get("text", "")
        label_sv = f" — {_shorten(label, 100)}" if label and not label.startswith("Reservation") else ""
        nej_sv = f"Stöd för motförslaget: reservation {n}{who_sv}{label_sv}."
        nej_en = f"Backing the counter-proposal: reservation {n}{who_en}{label_sv}."
    else:
        nej_sv = "Att avvisa utskottets förslag."
        nej_en = "Rejecting the committee proposal."

    return {
        "ja_sv": ja_sv,
        "nej_sv": nej_sv,
        "avstar_sv": "Partiet markerar en egen linje som inte finns med i slutomröstningens två alternativ.",
        "ja_en": ja_en,
        "nej_en": nej_en,
        "avstar_en": "The party marks a position of its own that is not one of the two final alternatives.",
    }
