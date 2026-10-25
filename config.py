"""
Central config: reads .env, loads the user profile, and defines which
sources to scrape on each run.

Edit the SOURCES list to add/remove companies.
"""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).parent

# Load .env if present
load_dotenv(ROOT / ".env")

# --- Secrets / model selection -------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
# Lead lookup (HQ city + sector) is a recall task, so it runs on its own cheap model
LEAD_LOOKUP_MODEL = os.getenv("LEAD_LOOKUP_MODEL", "claude-haiku-5-5")
# Translating job locations to countries is a lookup too (see job_radar/geo.py)
LOCATION_MODEL = os.getenv("LOCATION_MODEL", "claude-haiku-5-5")
DB_PATH = ROOT / os.getenv("DB_PATH", "jobs.db")
LEADS_DB_PATH = ROOT / os.getenv("LEADS_DB_PATH", "leads.db")
FEEDBACK_PATH = ROOT / "feedback.json"
SOURCES_EXTRA_PATH = ROOT / "sources_extra.json"
# Sources switched off on the Sources page: not fetched, and their roles hidden
SOURCES_DISABLED_PATH = ROOT / "sources_disabled.json"
PROFILE_PATH = ROOT / "profile.json"
# Shows a "Fetch jobs" button on the local dashboard. Only for copies that
# fetch on this laptop: where CI owns jobs.db (the live setup), a local fetch
# changes the committed database and causes merge conflicts.
LOCAL_FETCH = os.getenv("LOCAL_FETCH", "").strip() == "1"
LOCATIONS_CACHE_PATH = ROOT / "locations.json"
# Lead HQ locations → map coordinates (leads/places.py); local only, like leads.db
LEAD_PLACES_PATH = ROOT / "lead_places.json"

# --- Sources to scrape ---------------------------------------------------------
# Each entry is a dict with at minimum {"type": str, ...source-specific keys...}.
# The runner instantiates the right scraper class based on "type".
#
# Greenhouse boards: the "token" is the slug after /v1/boards/ in their URL.
#   e.g. boards.greenhouse.io/anthropic -> token is "anthropic"
# Lever postings: the "company" is the slug after lever.co/.
#   e.g. jobs.lever.co/figma -> company is "figma"
# Getro: the "subdomain" is the part before .getro.com.
#   e.g. indexventures.getro.com -> subdomain is "indexventures"

SOURCES: list[dict[str, Any]] = [
    # --- VC portfolio boards (Getro-powered) ---------------------------------
    {"type": "getro_json", "subdomain": "indexventures",                "collection_id": 1629,
     "label": "Index Ventures", "query": "design", "max_pages": 5},

    {"type": "getro_json", "domain":    "opportunities.northzone.com",  "collection_id": 3791,
     "label": "Northzone",      "query": "design", "max_pages": 5},

    {"type": "getro_json", "domain":    "careers.atomico.com",          "collection_id": 36986,
     "label": "Atomico",        "query": "design", "max_pages": 5},

    {"type": "getro_json", "domain":    "talent.seedcamp.com",          "collection_id": 4186,
     "label": "Seedcamp",       "query": "design", "max_pages": 5},

    # --- VC portfolio boards (Consider-powered) ----------------------------------
    {"type": "consider", "board_id": "localglobe-all",    "domain": "jobs.phoenixcourt.vc",  "label": "Phoenix Court"},
    {"type": "consider", "board_id": "notion-capital",    "domain": "jobs.notion.vc",        "label": "Notion Capital"},
    {"type": "consider", "board_id": "balderton-capital", "domain": "careers.balderton.com", "label": "Balderton"},

    # --- Direct Greenhouse boards (examples — replace with companies you target) ----
    # {"type": "greenhouse", "token": "anthropic", "label": "Anthropic"},
    # {"type": "greenhouse", "token": "monzo",     "label": "Monzo"},

    # --- Direct Lever boards (examples) --------------------------------------
    # {"type": "lever", "company": "figma", "label": "Figma"},

    # --- Ashby boards (slugs auto-discovered from Getro/Consider job URLs) ---
    # Slug = the identifier in https://jobs.ashbyhq.com/{slug}
    {"type": "ashby", "slug": "perk",            "label": "TravelPerk"},
    {"type": "ashby", "slug": "gamma",           "label": "Gamma"},
    {"type": "ashby", "slug": "sorare",          "label": "Sorare"},
    {"type": "ashby", "slug": "synthesia",       "label": "Synthesia"},
    {"type": "ashby", "slug": "topline-pro",     "label": "Topline Pro"},
    {"type": "ashby", "slug": "tandem-health",   "label": "Tandem Health"},
    {"type": "ashby", "slug": "hinge-health",    "label": "Hinge Health"},
    {"type": "ashby", "slug": "uipath",          "label": "UiPath"},
    {"type": "ashby", "slug": "fluidstack",      "label": "Fluidstack"},
    {"type": "ashby", "slug": "granola",         "label": "Granola"},
    {"type": "ashby", "slug": "marshmallow",     "label": "Marshmallow"},
    {"type": "ashby", "slug": "faculty",         "label": "Faculty"},
    {"type": "ashby", "slug": "Nivoda",          "label": "Nivoda"},
    {"type": "ashby", "slug": "duvo",            "label": "DUVO"},
    {"type": "ashby", "slug": "parallel",        "label": "Parallel Web Systems"},
    {"type": "ashby", "slug": "ideogram",        "label": "Ideogram AI"},
    {"type": "ashby", "slug": "neko-health",     "label": "Neko Health"},
    {"type": "ashby", "slug": "se3",             "label": "Se3 Labs"},
    {"type": "ashby", "slug": "primer.io",       "label": "Primer"},
    {"type": "ashby", "slug": "flowengineering", "label": "Flow Engineering"},
    {"type": "ashby", "slug": "envoy",           "label": "Envoy"},
    {"type": "ashby", "slug": "multiverse",      "label": "Multiverse"},
    {"type": "ashby", "slug": "motorway",        "label": "Motorway"},
    {"type": "ashby", "slug": "goodlord",        "label": "Goodlord"},
    {"type": "ashby", "slug": "paddle",          "label": "Paddle"},
    {"type": "ashby", "slug": "lendable",        "label": "Lendable"},
    {"type": "ashby", "slug": "writer",          "label": "Writer"},
    {"type": "ashby", "slug": "lawhive",         "label": "Lawhive"},
    {"type": "ashby", "slug": "checkly",         "label": "Checkly"},
    {"type": "ashby", "slug": "taktile",         "label": "Taktile"},
    {"type": "ashby", "slug": "photoroom",       "label": "PhotoRoom"},
    {"type": "ashby", "slug": "tacto",           "label": "Tacto"},
    {"type": "ashby", "slug": "wordsmith",       "label": "Wordsmith AI"},
    {"type": "ashby", "slug": "sylvera",         "label": "Sylvera"},
    {"type": "ashby", "slug": "notion",          "label": "Notion"},
    {"type": "ashby", "slug": "Kota",            "label": "Kota"},
    {"type": "ashby", "slug": "9fin",            "label": "9fin"},

    # --- Ashby boards (from Startupmap, senior → CDO range) ------------------
    {"type": "ashby", "slug": "lovable",        "label": "Lovable"},
    {"type": "ashby", "slug": "n8n",            "label": "n8n"},
    {"type": "ashby", "slug": "omnea",          "label": "Omnea"},
    {"type": "ashby", "slug": "nory",           "label": "Nory"},
    {"type": "ashby", "slug": "conveo",         "label": "Conveo"},
    {"type": "ashby", "slug": "fyxer",          "label": "Fyxer AI"},
    {"type": "ashby", "slug": "hoxhunt",        "label": "Hoxhunt"},
    {"type": "ashby", "slug": "zeno",           "label": "Zeno"},
    {"type": "ashby", "slug": "dash0",          "label": "Dash0"},
    {"type": "ashby", "slug": "birdie",         "label": "Birdie"},
    {"type": "ashby", "slug": "xyz-reality",    "label": "XYZ Reality"},
    {"type": "ashby", "slug": "tesslcareers",   "label": "Tessl"},
    {"type": "ashby", "slug": "ostrom",         "label": "Ostrom"},
    {"type": "ashby", "slug": "peec",           "label": "Peec"},
    {"type": "ashby", "slug": "voize",          "label": "Voize"},

    # --- Lever boards (from Startupmap, senior → CDO range) ------------------
    {"type": "lever", "company": "pigment",     "label": "Pigment"},
    {"type": "lever", "company": "matillion",   "label": "Matillion"},
    {"type": "lever", "company": "zeneducate",  "label": "Zen Educate"},

    # --- Greenhouse boards (from Startupmap, senior → CDO range) -------------
    {"type": "greenhouse", "token": "polyai",      "label": "PolyAI"},
    {"type": "greenhouse", "token": "prolific",    "label": "Prolific"},
    {"type": "greenhouse", "token": "tide",        "label": "Tide"},
    {"type": "greenhouse", "token": "wallapop",    "label": "Wallapop"},
    {"type": "greenhouse", "token": "yondrgroup",  "label": "Yondr Group"},

    # --- Removed 22 October 2026: boards returning 404 since summer ----------
    # Re-add one if its new board turns up (the Sources page shows at once
    # whether it works).
    #   DataSnipper (ashby "datasnipper.com") — roles also on the Index Ventures
    #     Getro board, already scraped
    #   Fresha (lever "fresha") — moved to Teamtailor, but its jobs feed
    #     redirects to fresha.com/careers, so it can't be scraped
    #   Lime (ashby "Lime"), Kittl (ashby "kittl"), Aegiron Resilience
    #     (ashby "aegiron"), Ona (ashby "ona") — new board not found
]

# --- Pre-filter behaviour ------------------------------------------------------
# Maximum number of jobs to send to Claude per run, after pre-filtering.
# A safety cap so a sudden surge of new jobs doesn't blow your API budget.
MAX_JOBS_TO_SCORE_PER_RUN = 200

# Maximum age (in days) for a job to be considered fresh enough to score.
# Uses posted_at when the source provides it, else falls back to discovered_at.
# Set to None to disable the freshness filter entirely.
MAX_JOB_AGE_DAYS = 15

# Score threshold (0–100) above which a job appears prominently on the dashboard
INTERESTING_SCORE_THRESHOLD = 65


def load_profile() -> dict[str, Any]:
    """Load the user profile. Falls back to profile.example.json if profile.json missing."""
    primary = PROFILE_PATH
    fallback = ROOT / "profile.example.json"
    path = primary if primary.exists() else fallback
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sources_extra() -> list[dict[str, Any]]:
    """Load dynamically-added company sources (e.g. promoted leads).

    Returns an empty list if sources_extra.json doesn't exist yet.
    """
    if not SOURCES_EXTRA_PATH.exists():
        return []
    with open(SOURCES_EXTRA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_disabled_sources() -> set[tuple[str, str]]:
    """Sources switched off on the Sources page, as (source, label) pairs.

    The same pair identifies a source in source_runs and on every job, so the
    switch matches exactly what the page shows. Empty if the file doesn't exist.
    """
    if not SOURCES_DISABLED_PATH.exists():
        return set()
    with open(SOURCES_DISABLED_PATH, "r", encoding="utf-8") as f:
        return {(d["source"], d["label"]) for d in json.load(f)}


def add_source_extra(source: dict[str, Any]) -> None:
    """Append a company source to sources_extra.json without editing config.py.

    Silently skips if an entry with the same type+slug/token/company already exists.
    """
    existing = load_sources_extra()
    key_fields = ("type", "slug", "token", "company", "subdomain")
    for s in existing:
        if all(s.get(k) == source.get(k) for k in key_fields if source.get(k)):
            return
    existing.append(source)
    with open(SOURCES_EXTRA_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
