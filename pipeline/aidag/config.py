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

VOTE_VALUES = ["Ja", "Nej", "Avstår", "Frånvarande"]

# Simulation defaults. Pilot runs one arm per model; full run picks by eval.
DEFAULT_MODEL = "claude-opus-4-8"
PILOT_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6"]
# p1 = pilot (motivering <=200 words); p2 = short motivering (2-4 sentences);
# p3 = p2 + decisive-first citations with per-citation "princip" labels
PROMPT_VERSION = "p3"
BATCH_CHUNK_SIZE = 300

RIKSDAG_ATTRIBUTION = "Källa: Sveriges riksdag"
