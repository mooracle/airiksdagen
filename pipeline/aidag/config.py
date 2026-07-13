"""Central configuration: paths, constants, party metadata, model settings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
INTERIM_DIR = DATA_DIR / "interim"
CORPUS_DIR = DATA_DIR / "corpus"
KB_DIR = DATA_DIR / "kb" / "snapshots"
RESULTS_DIR = DATA_DIR / "results"
SITE_DATA_DIR = REPO_ROOT / "site" / "src" / "data"

# Riksmöten covered by the 2022–2026 electoral period.
RIKSMOTEN = ["2022/23", "2023/24", "2024/25", "2025/26"]

# Bulk vote dumps, one per riksmöte. Källa: Sveriges riksdag.
VOTERING_ZIP_URL = "https://data.riksdagen.se/dataset/votering/votering-{rm}.json.zip"
DOKUMENTSTATUS_URL = "https://data.riksdagen.se/dokumentstatus/{dok_id}.json"

def rm_slug(rm: str) -> str:
    """'2022/23' -> '202223' (used in dump filenames)."""
    return rm.replace("/", "")

# The eight Riksdag parties of the 2022–2026 period.
# Colors follow common Swedish media conventions.
PARTIES: dict[str, dict] = {
    "S":  {"name": "Socialdemokraterna",   "color": "#E8112d", "bloc": "opposition"},
    "M":  {"name": "Moderaterna",          "color": "#52BDEC", "bloc": "government"},
    "SD": {"name": "Sverigedemokraterna",  "color": "#DDDD00", "bloc": "support"},
    "C":  {"name": "Centerpartiet",        "color": "#009933", "bloc": "opposition"},
    "V":  {"name": "Vänsterpartiet",       "color": "#DA291C", "bloc": "opposition"},
    "KD": {"name": "Kristdemokraterna",    "color": "#000077", "bloc": "government"},
    "MP": {"name": "Miljöpartiet",         "color": "#83CF39", "bloc": "opposition"},
    "L":  {"name": "Liberalerna",          "color": "#006AB3", "bloc": "government"},
}
PARTY_CODES = list(PARTIES)

# Hemicycle seating order, left to right (conventional political ordering).
HEMICYCLE_ORDER = ["V", "S", "MP", "C", "L", "KD", "M", "SD"]

# Tidöavtalet applies to the governing side from its signing date onward.
TIDO_DATE = "2022-10-14"
TIDO_SIGNATORIES = {"M", "KD", "L"}   # governing parties bound by the agreement
TIDO_SUPPORT = {"SD"}                  # support party to the agreement

# --- p5 context arm: each party's OWN long-form documents, date-gated ---------
#
# The p4 corpus (valmanifest + Tidöavtalet) is thin for the opposition: MP has
# 3,681 words and 29% of its votes come back coverage="not_covered". p5 adds two
# document classes, each visible ONLY from its own publication date onward — the
# same rule Tidöavtalet already follows via tido_applies().
#
# Why date-gating rather than one cutoff: a party programme adopted in 2025 was
# written AFTER (and partly BECAUSE OF) the votes we are reconstructing, so
# showing it to a 2023 vote leaks the outcome. But by 2025 it genuinely is the
# party's position. Gating on adoption date gives "the real state of the party"
# at each moment with no leakage in either direction.
#
# Party programmes (partiprogram / principprogram / idéprogram) — the party's
# foundational document, 5-20x its election manifesto. Every party gets its own
# and no one else's, so the coalition line is never handed to a party that is
# not bound by it.
#
# TRAP: S/M keep their programme on their own domain, but SD, KD, MP, L and V
# have all since REPLACED theirs, and their live sites now serve POST-election
# text. Scraping a party site for "the" programme silently violates the vintage
# rule. SND (Swedish National Data Service, Univ. of Gothenburg) archives the
# 2022-era versions the parties removed.
#
# Each entry: versions ordered oldest-first; `from` is the adoption date and a
# version is visible to votes on or after it.
# `from` is the ADOPTION date, never the file's publication date — L's 2023 PDF
# was posted 15 months after the landsmöte that amended it, and KD's in 2026.
# Where only the congress week is known, we gate on its END date: that can lag
# reality by a day or two but can never leak forward.
PARTY_PROGRAMS: dict[str, list[dict]] = {
    "S":  [{"title": "Ett program för förändring", "from": "2013-04-07",
            "url": "https://www.socialdemokraterna.se/download/18.12ce554f16be946d04640b87/1568881590520/ett-program-for-forandring_2013.pdf"},
           # re-adopted at the Göteborg congress, 28 May-1 Jun 2025
           {"title": "Socialdemokraternas partiprogram 2025", "from": "2025-06-01",
            "url": "https://www.socialdemokraterna.se/download/18.66b0e5c8197581879ca15ce/1749742402637/Socialdemokraternas%20partiprogram%202025.pdf"}],
    "M":  [{"title": "Frihet och ansvar – Ett moderat idéprogram för 2020-talet", "from": "2021-10-01",
            "url": "https://moderaterna.se/app/uploads/2022/01/Ideprogram_digitalt_9dec.pdf"}],
    "SD": [{"title": "Sverigedemokraternas principprogram 2019", "from": "2019-11-01",
            "url": "https://snd.se/sv/vivill/file/sd/c/2019/pdf"},
           # revised at Landsdagarna, Västerås, 23-26 Nov 2023
           {"title": "Sverigedemokraternas principprogram 2023", "from": "2023-11-26",
            "url": "https://www.sd.se/wp-content/uploads/2024/01/sverigedemokraternas-principprogram-2023-1.pdf"}],
    "C":  [{"title": "En hållbar framtid – Våra idéer gör skillnad", "from": "2013-03-01",
            "url": "https://snd.se/sv/vivill/file/c/i/2013/pdf"}],
    "V":  [{"title": "Vänsterpartiets partiprogram", "from": "2016-05-08",
            "url": "https://www.vansterpartiet.se/wp-content/uploads/2022/07/partiprogram_v-1jun2017.pdf"},
           # first full re-adoption since 2004 — congress, Jönköping, 8-12 May 2024
           {"title": "Vänsterpartiets partiprogram 2024", "from": "2024-05-12",
            "url": "https://www.vansterpartiet.se/wp-content/uploads/2024/11/partiprogram_2024_skrivare.pdf"}],
    "KD": [{"title": "Principprogram", "from": "2015-10-01",
            "url": "https://snd.se/sv/vivill/file/kd/c/2015/pdf"},
           # riksting 2025 "Ärende 30 – Uppdatering av principprogrammet": an
           # amendment, 96% identical to 2015, but it rewrites NATO/värnplikt,
           # citizenship and migration — the sections that matter most here.
           {"title": "Principprogram (rev. 2025)", "from": "2025-11-16",
            "url": "https://kristdemokraterna.se/download/18.7932b3db19c9887db6c221c/1773136182639/Principprogram%20hemsida.pdf"}],
    "MP": [{"title": "Miljöpartiets partiprogram", "from": "2013-01-01",
            "url": "https://www.mp.se/wp-content/uploads/2022/03/miljopartiets_partiprogram_lagupplost_2013.pdf"},
           # congress, Västerås, 17-19 Oct 2025
           {"title": "Miljöpartiets partiprogram 2025", "from": "2025-10-19",
            "url": "https://www.mp.se/wp-content/uploads/2025/12/miljopartiets-partiprogram-2025.pdf"}],
    # L amends rather than re-adopts; three consolidations span the term.
    "L":  [{"title": "Frihet i globaliseringens tid (uppdaterat t.o.m. 2021)", "from": "2021-11-21",
            "url": "https://www.liberalerna.se/wp-content/uploads/liberalernas-partiprogram-2022-1.pdf"},
           # landsmöte, Linköping, 17-19 Nov 2023
           {"title": "Liberalernas partiprogram (uppdateringar 2015–2023)", "from": "2023-11-19",
            "url": "https://www.liberalerna.se/wp-content/uploads/liberalernas-partiprogram-2022docx-komprimerad.pdf"},
           # landsmöte, Karlstad, 21-23 Nov 2025
           {"title": "Liberalernas partiprogram 2025", "from": "2025-11-23",
            "url": "https://www.liberalerna.se/wp-content/uploads/liberalernas-partiprogram-2025.pdf"}],
}

# Shadow budgets (budgetmotioner). ONLY S, V, C and MP file one — M/KD/L present
# the actual budgetproposition, and SD (the Tidö support party) backs it rather
# than tabling an alternative. So the four parties with a shadow budget are
# exactly the four with the thinnest manifestos.
#
# LEAK: on the FiU1 divisions the chamber votes on these very motions. A party
# shown its own budget while voting on it has been handed the answer — the same
# reason reservation authorship is scrubbed. budget_excluded_for() drops it for
# precisely those cases, keyed on dok_id from the RAW dokumentstatus references
# (cases.parquet truncates its reference list at 20 and cannot be trusted here).
BUDGET_MOTIONS: dict[tuple[str, str], dict] = {
    ("V",  "2022/23"): {"dok_id": "HA021299", "bet": "2022/23:1299", "from": "2022-11-22"},
    ("S",  "2022/23"): {"dok_id": "HA022074", "bet": "2022/23:2074", "from": "2022-11-23"},
    ("C",  "2022/23"): {"dok_id": "HA022180", "bet": "2022/23:2180", "from": "2022-11-23"},
    ("MP", "2022/23"): {"dok_id": "HA022275", "bet": "2022/23:2275", "from": "2022-11-23"},
    ("V",  "2023/24"): {"dok_id": "HB022385", "bet": "2023/24:2385", "from": "2023-10-05"},
    ("S",  "2023/24"): {"dok_id": "HB022654", "bet": "2023/24:2654", "from": "2023-10-05"},
    ("MP", "2023/24"): {"dok_id": "HB022689", "bet": "2023/24:2689", "from": "2023-10-05"},
    ("C",  "2023/24"): {"dok_id": "HB022733", "bet": "2023/24:2733", "from": "2023-10-05"},
    ("V",  "2024/25"): {"dok_id": "HC021924", "bet": "2024/25:1924", "from": "2024-10-03"},
    ("C",  "2024/25"): {"dok_id": "HC022962", "bet": "2024/25:2962", "from": "2024-10-04"},
    ("S",  "2024/25"): {"dok_id": "HC023199", "bet": "2024/25:3199", "from": "2024-10-04"},
    ("MP", "2024/25"): {"dok_id": "HC023220", "bet": "2024/25:3220", "from": "2024-10-04"},
    ("V",  "2025/26"): {"dok_id": "HD022792", "bet": "2025/26:2792", "from": "2025-10-06"},
    ("S",  "2025/26"): {"dok_id": "HD023551", "bet": "2025/26:3551", "from": "2025-10-07"},
    ("MP", "2025/26"): {"dok_id": "HD023770", "bet": "2025/26:3770", "from": "2025-10-07"},
    ("C",  "2025/26"): {"dok_id": "HD023811", "bet": "2025/26:3811", "from": "2025-10-07"},
}
BUDGET_PARTIES = {"S", "V", "C", "MP"}

VOTE_VALUES = ["Ja", "Nej", "Avstår", "Frånvarande"]

# Simulation defaults. Pilot runs one arm per model; full run picks by eval.
DEFAULT_MODEL = "claude-opus-4-8"
PILOT_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6"]
# p1 = pilot (motivering <=200 words); p2 = short motivering (2-4 sentences);
# p3 = p2 + decisive-first citations with "princip" labels;
# p4 = p3 + per-date worldstate context with structured omvarld references
# p5 = p4 + each party's OWN long-form documents, date-gated: partiprogram (all
#      parties, version rolls over mid-term) and budgetmotion (S/V/C/MP only).
#      p4 starved the opposition — MP reasoned from 3,681 words and returned
#      not_covered on 29% of its votes. See docs/orchestration-full-v3.md.
# p5 is CURRENT. p4 is kept only so the older committed runs still verify.
PROMPT_VERSION = "p5"
BATCH_CHUNK_SIZE = 300

RIKSDAG_ATTRIBUTION = "Källa: Sveriges riksdag"
