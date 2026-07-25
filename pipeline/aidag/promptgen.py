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

from aidag.config import (
    CORPUS_DIR,
    KB_DIR,
    PARTIES,
    PROMPT_VERSION,
    TIDO_DATE,
    TIDO_SIGNATORIES,
    TIDO_SUPPORT,
)
from aidag.corpus import DOCS_P4, docs_for_version, documents_for, tido_applies  # noqa: F401

SV_MONTH_NAMES = [
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
]

def decision_schema(prompt_version: str = PROMPT_VERSION) -> dict:
    """The schema for one decision. The citable-document enum is exactly the set
    of documents that prompt version can ever put in context — p5 adds the party
    programme and the shadow budget; p6 narrows it to the party's own plan."""
    import copy

    base = DECISION_SCHEMA_P6 if prompt_version >= "p6" else DECISION_SCHEMA
    schema = copy.deepcopy(base)
    schema["properties"]["citations"]["items"]["properties"]["document"]["enum"] = list(
        docs_for_version(prompt_version)
    )
    return schema


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
                    # only documents actually provided in context — citing
                    # anything else would be uncheckable against a source
                    "document": {
                        "type": "string",
                        "enum": list(DOCS_P4),
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

def _p6_schema() -> dict:
    """p6 splits policy stance from parliamentary behaviour.

    `rost` is gone: the vote is DERIVED from hallning in code (utskottet→Ja,
    motforslaget→Nej, ingendera→Avstår), never predicted. In full-v3, 40.8% of
    all disagreements with the real vote were cases where the party abstained
    tactically, and the agent under-predicted Avstår 2.3x — asking party plans
    to forecast floor tactics is a category error. The gap between the derived
    vote and the real one is the product, computed at analysis time.

    Two values, not three. A 55-decision Opus gate ran a three-valued version
    with an `ingendera` ("the plan backs neither side") option: it fired 0/55,
    including on all 15 real abstentions. Principled abstention is not something
    party plans express, so the third value was dead weight.

    `hallning` is a stance on the reservation's DEMAND, not on "which side".
    The same gate showed the two sides are not the same kind of claim: the
    committee's position is often procedural ("an inquiry is ongoing, do not
    preempt it") while the reservation is substantive ("do X now"). Plans speak
    to substance and are silent on procedure, so asking which side the plan
    backed sent 62% of stances to the reservation against a real 44% Nej rate.
    `plan_tacker_utskottets_skal` measures that asymmetry directly instead of
    leaving it to sit inside the stance as an unmeasured bias.
    """
    import copy

    s = copy.deepcopy(DECISION_SCHEMA)
    p = s["properties"]
    p.pop("rost")
    # dict ordering is the field order the model sees — stance first
    s["properties"] = {
        "hallning": {"type": "string", "enum": ["stodjer", "avvisar"]},
        **p,
        "plan_tacker_utskottets_skal": {"type": "string", "enum": ["ja", "nej"]},
    }
    # Extrapolation is allowed — the actor may reason from a nearby principle —
    # but it must always stay traceable: every decision cites at least one
    # verbatim passage, so a reader can see what it reasoned FROM even when the
    # plan does not address the case directly. p5 let `not_covered` return no
    # citation at all; across 135 gate decisions the model never used that
    # escape, so making it structural costs nothing and removes the hole.
    s["properties"]["citations"]["minItems"] = 1
    s["required"] = [
        "hallning", "confidence", "coverage", "motivering", "citations",
        "omvarld", "flags", "plan_tacker_utskottets_skal",
    ]
    return s


DECISION_SCHEMA_P6 = _p6_schema()

# stödjer the demand => you are backing the reservation => Nej on the floor
HALLNING_TO_ROST = {"stodjer": "Nej", "avvisar": "Ja"}


def derive_rost(hallning: str) -> str:
    """The vote the party's own targets imply.

    This is the separate actor's vote, not a forecast of the party. The actor
    decides on party targets alone and has no access to parliamentary tactics,
    so it never abstains — abstention is something only the real party does.
    """
    return HALLNING_TO_ROST[hallning]


# How much weight a target-vs-vote difference can carry. Derived in code from
# fields the model already produces, so the label is consistent and cannot be
# hallucinated. Extrapolation is publishable — mislabelled extrapolation is not.
EVIDENCE_TIERS = {
    "explicit": "the plan states this commitment outright",
    "extrapolated": "derived from a nearby principle in the plan",
    "off_axis": "the plan does not speak to what this vote turned on",
}


def evidence_tier(decision: dict) -> str:
    """Label one actor decision by how well the party's plan reaches the case.

    `off_axis` is the honest majority case: across 135 gate decisions the plan
    was silent on the committee's actual reason 54-69% of the time, because
    committee positions are often procedural ("an inquiry is ongoing") while
    plans speak to substance. Such a decision is still a real actor vote and
    still fully cited — it just cannot carry a claim that the party broke a
    specific promise.
    """
    off_axis = decision.get("plan_tacker_utskottets_skal") == "nej"
    explicit = decision.get("coverage") == "explicit"
    if explicit and decision.get("confidence") in ("high", "medium"):
        return "explicit"
    return "off_axis" if off_axis else "extrapolated"


ROLE_PROMPT_P6 = """\
Du är ett analytiskt verktyg i ett partipolitiskt obundet forskningsprojekt. Din uppgift: \
avgör vilken av två ståndpunkter i en riksdagsvotering som partiet {party_name} ({code}) \
BORDE stå bakom — uteslutande utifrån partiets egna plandokument som återges nedan, och \
lägesbilden av landet vid tidpunkten. Detta är en rekonstruktion av plantrohet, inte en \
förutsägelse av partiets agerande i kammaren.

Regler:
- Grunda ställningstagandet ENBART på dokumenten nedan. Använd inte kunskap om hur partiet \
faktiskt agerade i riksdagen, uttalanden i media, eller händelser efter tidpunkten.
- Väg INTE in partitaktik: regeringsunderlag, koalitionslojalitet, uppgörelser, \
utskottsdiscipline eller hur andra partier väntas rösta. Frågan är vad partiets EGEN plan \
säger i sakfrågan — inget annat.
- Om dokumenten inte täcker frågan: härled från dokumentens principer och ange \
coverage="inferred", eller ange coverage="not_covered" och utgå från närmast liggande princip.
- Citera ordagrant ur dokumenten i citations; citaten måste vara exakta utdrag. \
Välj det STÄLLE i dokumenten som faktiskt bär ställningstagandet — inte en allmän \
formulering. Ordna citaten efter vikt: det FÖRSTA citatet är det avgörande. Ge varje citat \
en kort "princip" (2–6 ord) som namnger åtagandet, t.ex. "minskad asylinvandring" eller \
"höjda försvarsanslag".

Fältet hallning — partiets plan i förhållande till motförslagets KRAV (inte till \
vilken sida som helst):
- "stodjer" = planen stöder det som motförslaget kräver i sak.
- "avvisar" = planen talar emot det som motförslaget kräver i sak.

Fältet plan_tacker_utskottets_skal — utskottets ställningstagande och motförslaget är \
ofta inte samma sorts påstående. Motförslaget kräver något i sak; utskottets skäl är \
ofta procedurella ("en utredning pågår, arbetet bör inte föregripas", "frågan bereds"). \
Partiplaner uttalar sig om sak, sällan om beredningsordning.
- "ja" = planen säger något om just det skäl utskottet anför.
- "nej" = planen är tyst om utskottets skäl (t.ex. planen kräver X i sak, medan \
utskottet enbart invänder att arbete redan pågår). Sätt då "nej" ÄVEN om du satt \
hallning="stodjer" — de två frågorna är olika.

Osäkerhet är ett giltigt och förväntat svar:
- Sätt confidence="low" när planen bara ger allmänna principer att härleda ur.
- Sätt coverage="not_covered" när planen inte alls behandlar sakfrågan; välj då ändå \
hallning utifrån närmast liggande princip och sätt confidence="low".
- Pressa inte fram ett säkert svar ur ett svagt underlag.
- Ange ALLTID minst ett citat, även när du härleder eller när coverage="not_covered": \
citera då den närmast liggande princip du faktiskt utgått från. Att härleda är tillåtet \
— att göra det utan att visa vad du utgått från är det inte.

Omvärldsläget ("Läget i landet och omvärlden" nedan) är inkommande läge som partiet \
inte kunde planera för. Regler för omvärlden:
- Dokumenten är alltid grunden. Omvärlden får vägas in ENDAST när den väsentligt \
påverkar hur dokumenten ska tillämpas (krisåtgärder, situationer planen aldrig förutsåg).
- Om omvärlden vägts in: sätt omvarld.paverkar=true och ange max 3 faktorer med \
kort effekt. Annars omvarld.paverkar=false och tom lista.
- Om dokumenten saknar svar och omvärlden avgör: sätt coverage="not_covered" OCH \
omvarld.paverkar=true.

Svara med JSON enligt schemat. Motiveringen skrivs på svenska och ska vara KORT: \
2–4 meningar (max ca 80 ord) som anger det avgörande åtagandet i dokumenten och hur \
det leder till ställningstagandet."""


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


P5_ROLE_DOCS = """\

Dokumenten nedan är partiets egna. Valmanifestet är det färskaste och mest \
bindande uttrycket för partiets linje i den här mandatperioden; partiprogrammet \
anger de långsiktiga principerna. Där de skiljer sig väger valmanifestet tyngst."""


@lru_cache(maxsize=32)
def _corpus_text(filename: str) -> str:
    return (CORPUS_DIR / filename).read_text().lstrip("﻿").strip()


def build_system_blocks(
    code: str,
    datum: str,
    rm: str = "",
    votering_id: str = "",
    prompt_version: str = PROMPT_VERSION,
) -> list[dict]:
    """System blocks for one party at one date.

    Byte-identical for every case sharing a context_key, with a 1h cache
    breakpoint on the last corpus block. Which documents appear is decided by
    corpus.documents_for — the same function the verify gate uses, so an agent
    can never be blamed for citing a document it was given, nor credited for one
    it never saw.
    """
    party_name = PARTIES[code]["name"]
    if prompt_version >= "p6":
        # No Tidöavtalet block in p6, so no Tidö role addendum either — it would
        # tell a governing party about a document it is no longer shown, and the
        # run is measuring fidelity to the party's own plan, not to the coalition.
        role = ROLE_PROMPT_P6.format(party_name=party_name, code=code) + P5_ROLE_DOCS
    else:
        role = ROLE_PROMPT.format(party_name=party_name, code=code)
        if tido_applies(code, datum):
            extra = TIDO_ROLE_SIGNATORY if code in TIDO_SIGNATORIES else TIDO_ROLE_SUPPORT
            role += extra.format(party_name=party_name)
        if prompt_version >= "p5":
            role += P5_ROLE_DOCS

    blocks = [{"type": "text", "text": role}]
    for _kind, tag, text in documents_for(code, datum, rm, votering_id, prompt_version):
        open_tag = tag.split(" ")[0]
        blocks.append({"type": "text", "text": f"<{tag}>\n{text}\n</{open_tag}>"})
    blocks[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return blocks


import re

# Riksdag document references: numeric motion refs ("2023/24:78", "…:78a") AND
# alpha-prefixed betänkande/committee refs ("2025/26:RS5", "2023/24:AU10"). Both
# are memorization keys and must be masked; the earlier numeric-only pattern let
# the alpha-prefixed form slip into the case text uncaught.
DOCREF_RE = re.compile(r"\b\d{4}/\d{2}:(?:[A-Za-zÅÄÖ]{1,5})?\d+[a-z]?\b")
# "…avslår motion 2022/23:2372 av Jonny Cato och Helena Vilhelmsson (båda C)." —
# the author clause leaks reservation authorship; strip it in the anonymous arm.
# The middle allows any non-paren, non-newline char (so abbreviations like
# "m.fl." / initials with periods don't break the match) but stays on one line
# so a clause can't span into the next motion (they are newline-separated).
AUTHOR_RE = re.compile(r"\s+av\s+[^()\n]{2,200}?\((?:båda\s+|samtliga\s+|bådas\s+)?[A-ZÅÄÖ]{1,3}(?:,\s*[A-ZÅÄÖ]{1,3})*\)")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Party authorship tag "(S)" … "(MP)" — must NEVER survive an anonymous render.
# `verify prompts` asserts this across the whole corpus; the labeled arm keeps it.
PARTY_TAG_RE = re.compile(r"\((?:S|M|SD|C|V|KD|MP|L)\)")


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


@lru_cache(maxsize=1)
def _reservations_layer() -> dict:
    """Party-blind reservation substance keyed 'votering_id:alt_id'.

    Cached for the process: batch renderers (simulate/agent-run/verify/dump) are
    separate processes from the reservations CLI, so the JSONL is stable while a
    run renders. The layer is optional — if it is absent the prompt falls back to
    the opaque "Reservation N" label.
    """
    try:
        from aidag.reservations import load_reservations

        return load_reservations()
    except Exception:  # noqa: BLE001 — layer is optional; degrade to raw label
        return {}


def _reservation_substance_sv(case: dict, alt: dict) -> str | None:
    """Swedish substance of one counter-proposal — what it actually argues, with
    authorship/party scrubbed. Taken from the alt itself (site export attaches it)
    or from the reservation-substance layer (parquet path). None if not recovered."""
    sub = alt.get("substance")
    if not sub:
        rec = _reservations_layer().get(f"{case.get('votering_id', '')}:{alt.get('alt_id')}")
        sub = rec.get("subject") if rec else None
    return (sub or {}).get("sv") or None


@lru_cache(maxsize=1)
def _casemeta_agent() -> dict:
    """The party-blind agent slice of the casemeta layer, keyed by votering_id."""
    try:
        from aidag.casemeta import load_casemeta

        return {v: (r.get("agent") or {}) for v, r in load_casemeta().items()}
    except Exception:  # noqa: BLE001 — layer absent: p6 falls back to p5 rendering
        return {}


def _render_p6_arende(case: dict, arm: str) -> list[str] | None:
    """p6 case block, built from the casemeta brief.

    p5 gave the agent a betänkande-wide `utsknotis` and a hollow
    `Utskottets förslag: Riksdagen avslår motionerna [nr], [nr]…` — a median of
    92 characters, mostly masked document numbers, for the Ja side. The casemeta
    agent view carries the committee's actual reasoning (~287 chars) and each
    reservation's demand (~336 chars), both already de-leaked. Returns None when
    a case has no record, so the caller falls back to the p5 block.
    """
    ag = _casemeta_agent().get(case["votering_id"]) or {}
    committee = (ag.get("committee") or {}).get("sv", "").strip()
    alts = [a for a in (ag.get("alternatives") or []) if (a.get("sv") or "").strip()]
    if not committee or not alts:
        return None
    parts = ["<arende>", f"Utskott: {case['utskott']}", f"Ärende: {case['rubrik']}"]
    parts.append(f"Utskottets ställningstagande: {scrub_text(committee, arm)}")
    parts.append("Motförslag i voteringen:")
    for i, alt in enumerate(alts):
        label = chr(ord("A") + i)
        body = scrub_text(alt["sv"].strip(), arm)
        if arm == "labeled" and alt.get("source_partier"):
            parts.append(f"- Alternativ {label} ({', '.join(alt['source_partier'])}): {body}")
        else:
            parts.append(f"- Alternativ {label}: {body}")
    parts.append("</arende>")
    return parts


def render_user_message(case: dict, arm: str = "anonymous", prompt_version: str = PROMPT_VERSION) -> str:
    if prompt_version >= "p6":
        head = [f"Tidpunkt: {coarse_time(case['datum'])}."]
        if ctx := (render_worldstate_block(case["datum"]) or render_kb_block(case["kb_month"])):
            head.append(ctx)
        if block := _render_p6_arende(case, arm):
            head += block
            head.append(
                "Stödjer eller avvisar partiets egen plan det som motförslaget kräver "
                "i sak (stodjer/avvisar)? Ange också om planen alls berör utskottets skäl."
            )
            return "\n".join(head)
        # no casemeta record — fall through to the p5 rendering below

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
            # Show what the counter-proposal actually argues (party-blind, recovered
            # by the reservation-substance layer) instead of the opaque "Reservation N",
            # so the agent can reason about a Nej on substance. Already de-leaked at
            # ingest; scrubbed again here as defence-in-depth. Falls back to the raw
            # label for cases the layer has not covered yet.
            body = scrub_text(_reservation_substance_sv(case, alt) or alt["text"], arm)
            if arm == "labeled" and alt.get("source_partier"):
                who = ", ".join(alt["source_partier"])
                parts.append(f"- Alternativ {label} ({who}): {body}")
            else:
                parts.append(f"- Alternativ {label}: {body}")
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
