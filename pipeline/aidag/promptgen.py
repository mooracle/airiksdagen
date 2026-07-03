"""Render simulation prompts: party corpus system blocks + per-case user messages.

Design decisions (see docs/methodology):
- Full documents in context, no retrieval — removes RAG as a confound and
  maximizes prompt-cache reuse (the per-party corpus prefix is byte-identical
  across all of that party's requests).
- Identifier stripping — no dok_id/beteckning/votering_id/exact dates in the
  prompt; time context is coarse ("november 2023"). Reduces verbatim recall of
  memorized outcomes (contamination is measured separately by the probe).
- Anonymous alternatives — reservation texts are shown without party
  authorship ("Alternativ A/B"); the "labeled" pilot arm keeps authorship so
  the effect of anonymization can be measured and published.
"""

from __future__ import annotations

import json
from functools import lru_cache

from aidag.config import CORPUS_DIR, KB_DIR, PARTIES, TIDO_DATE, TIDO_SIGNATORIES, TIDO_SUPPORT

SV_MONTH_NAMES = [
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
]

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "rost": {"type": "string", "enum": ["Ja", "Nej", "Avstår"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "coverage": {"type": "string", "enum": ["explicit", "inferred", "not_covered"]},
        "motivering": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "enum": ["valmanifest", "partiprogram", "tidoavtalet"],
                    },
                    "quote": {"type": "string"},
                    "princip": {"type": "string"},
                },
                "required": ["document", "quote", "princip"],
                "additionalProperties": False,
            },
        },
        "omvarld": {
            "type": "object",
            "properties": {
                "paverkar": {"type": "boolean"},
                "faktorer": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "faktor": {"type": "string"},
                            "effekt": {"type": "string"},
                        },
                        "required": ["faktor", "effekt"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["paverkar", "faktorer"],
            "additionalProperties": False,
        },
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rost", "confidence", "coverage", "motivering", "citations", "omvarld", "flags"],
    "additionalProperties": False,
}

ROLE_PROMPT = """\
Du är ett analytiskt verktyg i ett partipolitiskt obundet forskningsprojekt. Din uppgift: \
avgör hur partiet {party_name} ({code}) BORDE rösta i en votering i Sveriges riksdag — \
uteslutande utifrån partiets egna dokument som återges nedan, och lägesbilden av landet \
vid tidpunkten. Detta är en rekonstruktion av dokumenttrohet, inte en förutsägelse.

Regler:
- Grunda beslutet ENBART på dokumenten nedan. Använd inte kunskap om hur partiet \
faktiskt agerade i riksdagen, uttalanden i media, eller händelser efter tidpunkten.
- Om dokumenten inte täcker frågan: härled från dokumentens principer och ange \
coverage="inferred", eller ange coverage="not_covered" och rösta utifrån närmast \
liggande princip.
- Citera ordagrant ur dokumenten i citations; citaten måste vara exakta utdrag. \
Välj det STÄLLE i dokumenten som faktiskt bär beslutet — inte en allmän formulering. \
Ordna citaten efter vikt: det FÖRSTA citatet är det avgörande. Ge varje citat en kort \
"princip" (2–6 ord) som namnger åtagandet, t.ex. "minskad asylinvandring" eller \
"höjda försvarsanslag".

Så fungerar voteringen (voteringsordning i kammaren):
- Ja = du stödjer utskottets förslag (beslutsförslaget).
- Nej = du stödjer motförslaget i den slutliga propositionen, eller avvisar förslaget.
- Avstår = partiet hade en egen linje (t.ex. en egen reservation) som inte står i det \
slutliga propositionsparet; att avstå markerar den egna linjen. Använd Avstår när \
dokumenten pekar på en tydligt egen position som varken utskottsförslaget eller \
motförslaget motsvarar.

Omvärldsläget ("Läget i landet och omvärlden" nedan) är inkommande läge som partiet \
inte kunde planera för. Regler för omvärlden:
- Dokumenten är alltid grunden för rösten. Omvärlden får vägas in ENDAST när den \
väsentligt påverkar hur dokumenten ska tillämpas (krisåtgärder, situationer planen \
aldrig förutsåg).
- Om omvärlden vägts in: sätt omvarld.paverkar=true och ange max 3 faktorer med \
kort effekt. Annars omvarld.paverkar=false och tom lista.
- Om dokumenten saknar svar och omvärlden avgör: sätt coverage="not_covered" OCH \
omvarld.paverkar=true.

Svara med JSON enligt schemat. Motiveringen skrivs på svenska och ska vara KORT: \
2–4 meningar (max ca 80 ord) som anger det avgörande åtagandet i dokumenten och hur \
det leder till Ja/Nej/Avstår."""

TIDO_ROLE_SIGNATORY = """\

Särskild kontext: {party_name} ingår i regeringsunderlaget och har undertecknat \
Tidöavtalet (återges nedan). Avtalet är en gemensam överenskommelse som partiet \
förbundit sig att genomföra; väg det mot partiets egna dokument där de skiljer sig."""

TIDO_ROLE_SUPPORT = """\

Särskild kontext: {party_name} är samarbetsparti till regeringen och har undertecknat \
Tidöavtalet (återges nedan) som grund för sitt stöd; väg det mot partiets egna dokument \
där de skiljer sig."""


@lru_cache(maxsize=32)
def _corpus_text(filename: str) -> str:
    return (CORPUS_DIR / filename).read_text().lstrip("﻿").strip()


def tido_applies(code: str, datum: str) -> bool:
    return code in (TIDO_SIGNATORIES | TIDO_SUPPORT) and datum >= TIDO_DATE


def build_system_blocks(code: str, datum: str) -> list[dict]:
    """System blocks for one party. Byte-identical within a party+Tidö-era,
    with a 1h cache breakpoint on the last corpus block."""
    party_name = PARTIES[code]["name"]
    role = ROLE_PROMPT.format(party_name=party_name, code=code)
    if tido_applies(code, datum):
        extra = TIDO_ROLE_SIGNATORY if code in TIDO_SIGNATORIES else TIDO_ROLE_SUPPORT
        role += extra.format(party_name=party_name)

    blocks = [
        {"type": "text", "text": role},
        {
            "type": "text",
            "text": f"<valmanifest_2022>\n{_corpus_text(f'valmanifest-2022-{code.lower()}.txt')}\n</valmanifest_2022>",
        },
    ]
    if tido_applies(code, datum):
        blocks.append({
            "type": "text",
            "text": f"<tidoavtalet>\n{_corpus_text('tidoavtalet-2022.txt')}\n</tidoavtalet>",
        })
    blocks[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return blocks


import re

DOCREF_RE = re.compile(r"\b\d{4}/\d{2}:\d+[a-z]?\b")
# "…avslår motion 2022/23:2372 av Jonny Cato och Helena Vilhelmsson (båda C)." —
# the author clause leaks reservation authorship; strip it in the anonymous arm.
AUTHOR_RE = re.compile(r"\s+av\s+[^.()]{2,120}?\((?:båda\s+|samtliga\s+)?[A-ZÅÄÖ]{1,3}(?:,\s*[A-ZÅÄÖ]{1,3})*\)")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def scrub_text(text: str, arm: str) -> str:
    """Remove memorization keys from Riksdag prose: doc numbers, exact dates,
    and (in the anonymous arm) motion-author clauses with party labels."""
    if arm != "labeled":
        text = AUTHOR_RE.sub("", text)
    text = DOCREF_RE.sub("[nr]", text)
    return ISO_DATE_RE.sub("[datum]", text)


@lru_cache(maxsize=64)
def _kb_snapshot(month: str) -> dict | None:
    path = KB_DIR / f"{month}.json"
    return json.loads(path.read_text()) if path.exists() else None


def render_kb_block(month: str) -> str:
    snap = _kb_snapshot(month)
    if snap is None:
        return ""
    lines = ["<laget_i_landet>"]
    gov = snap.get("government", {})
    if gov:
        koalition = "+".join(gov.get("koalition", []))
        stod = "+".join(gov.get("stodpartier", []))
        lines.append(
            f"Regering: {gov.get('statsminister')} ({koalition})"
            + (f", stödparti: {stod}" if stod else "")
        )
    for ind in snap.get("indicators", []):
        period = ind["period"][:7]  # coarse month only — no exact dates in prompts
        lines.append(f"{ind['label']}: {ind['value']} {ind['unit']} (avser {period})")
    # NOTE: snapshot["party_support"] (opinion polls) is deliberately NOT
    # rendered. The research question is whether parties follow their own
    # pre-election plans; giving agents polling data would let them adjust to
    # popularity instead of the documents. Polls are shown on the website only.
    events = snap.get("events", [])
    if events:
        lines.append("Händelser den senaste tiden:")
        for e in events:
            lines.append(f"- {scrub_text(e['text'], 'anonymous')}")
    lines.append("</laget_i_landet>")
    return "\n".join(lines)


def render_worldstate_block(datum: str) -> str:
    """Per-date worldstate (p4): indicators known before the vote date + the
    most salient events of the preceding ~30 days. Falls back to '' when the
    worldstate datasets are not built (caller then uses the monthly KB)."""
    from aidag.worldstate import worldstate_for

    snap = worldstate_for(datum)
    if snap is None:
        return ""
    from aidag.build_kb import government_at

    gov = government_at(datum)
    lines = ["<laget_i_landet_och_omvarlden>"]
    koalition = "+".join(gov.get("koalition", []))
    stod = "+".join(gov.get("stodpartier", []))
    lines.append(
        f"Regering: {gov.get('statsminister')} ({koalition})"
        + (f", stödparti: {stod}" if stod else "")
    )
    for ind in snap["indicators"]:
        period = ind["period"][:7]  # coarse month — no exact dates in prompts
        lines.append(f"{ind['label']}: {ind['value']} {ind['unit']} (avser {period})")
    if snap["events"]:
        lines.append("Senaste tidens händelser och frågor (urval):")
        for e in snap["events"]:
            lines.append(f"- {scrub_text(e['text'], 'anonymous')}")
    lines.append("</laget_i_landet_och_omvarlden>")
    return "\n".join(lines)


def coarse_time(datum: str) -> str:
    """'2023-11-15' -> 'november 2023' — no exact date reaches the prompt."""
    return f"{SV_MONTH_NAMES[int(datum[5:7]) - 1]} {datum[:4]}"


def render_user_message(case: dict, arm: str = "anonymous") -> str:
    parts = [f"Tidpunkt: {coarse_time(case['datum'])}."]
    # p4: per-date worldstate; monthly KB only as fallback if not built
    context = render_worldstate_block(case["datum"]) or render_kb_block(case["kb_month"])
    if context:
        parts.append(context)

    parts.append("<arende>")
    parts.append(f"Utskott: {case['utskott']}")
    parts.append(f"Ärende: {case['rubrik']}")
    if case.get("dok_titel") and case["dok_titel"] != case["rubrik"]:
        parts.append(f"Betänkande: {case['dok_titel']}")
    # utsknotis is the committee's PRE-decision summary; the post-decision
    # `notis` ("Riksdagen sa ja…") would leak the outcome and is never used here.
    if case.get("utsknotis"):
        parts.append(f"Sammanfattning: {scrub_text(case['utsknotis'], arm)}")
    parts.append(f"Utskottets förslag: {scrub_text(case['forslag_text'], arm)}")

    alternatives = case.get("alternatives") or []
    if isinstance(alternatives, str):
        alternatives = json.loads(alternatives)
    reservations = [a for a in alternatives if a["alt_id"] != "utskottet"]
    if reservations:
        parts.append("Motförslag i voteringen:")
        for i, alt in enumerate(reservations):
            label = chr(ord("A") + i)
            if arm == "labeled" and alt.get("source_partier"):
                who = ", ".join(alt["source_partier"])
                parts.append(f"- Alternativ {label} ({who}): {scrub_text(alt['text'], arm)}")
            else:
                parts.append(f"- Alternativ {label}: {scrub_text(alt['text'], arm)}")
    parts.append("</arende>")
    parts.append(
        "Hur borde partiet rösta i sakfrågan (Ja/Nej/Avstår), enligt sina egna dokument?"
    )
    return "\n".join(parts)


# Regexes that must NOT match a rendered prompt (asserted in tests and verify).
FORBIDDEN_PATTERNS = [
    r"\b[A-Z]{2}\d{2}[A-Z]{1,4}\d{1,3}\b",  # dok_id like HA01AU10
    r"\b\d{4}/\d{2}:[A-Za-zÅÄÖåäö]{1,5}\d{1,3}\b",  # beteckning like 2023/24:AU10
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",  # votering UUID
    r"\b\d{4}-\d{2}-\d{2}\b",  # exact ISO dates
]
