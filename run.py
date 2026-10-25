"""
Job Radar entry point.

Usage:
  python run.py fetch        # pull jobs from configured sources, score, save
  python run.py serve        # start the FastAPI dashboard at :8000
  python run.py serve --port 9000

Both commands read config from ./config.py and ./profile.json (or
./profile.example.json as fallback).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

import config as cfg  # noqa: E402  -- top-level config.py
from job_radar.geo import LocationResolver
from job_radar.matcher import ClaudeMatcher
from job_radar.runner import fetch_all, process
from job_radar.storage import Storage


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    log = logging.getLogger("run")

    profile = cfg.load_profile()
    storage = Storage(cfg.DB_PATH)

    matcher = None
    resolver = None
    if cfg.ANTHROPIC_API_KEY:
        matcher = ClaudeMatcher(api_key=cfg.ANTHROPIC_API_KEY, model=cfg.CLAUDE_MODEL)
        resolver = LocationResolver(cfg.ANTHROPIC_API_KEY, cfg.LOCATION_MODEL, cfg.LOCATIONS_CACHE_PATH)
    else:
        log.warning("No ANTHROPIC_API_KEY in environment — will fetch and pre-filter only")

    all_sources = cfg.SOURCES + cfg.load_sources_extra()
    log.info("Fetching from %d source(s)...", len(all_sources))
    fetch_start = datetime.now(timezone.utc)
    disabled = cfg.load_disabled_sources()
    jobs, source_runs = asyncio.run(fetch_all(all_sources, disabled))
    log.info("Total jobs across all sources: %d", len(jobs))

    stats = process(
        storage=storage,
        jobs=jobs,
        profile=profile,
        matcher=matcher,
        max_to_score=cfg.MAX_JOBS_TO_SCORE_PER_RUN,
        max_age_days=cfg.MAX_JOB_AGE_DAYS,
        force_rescore=args.force_rescore,
        fetch_start=fetch_start,
        feedback_path=cfg.FEEDBACK_PATH,
        resolver=resolver,
    )
    # Only once every job is saved with its new last-seen time: recording the
    # sources first, then failing, would make their jobs look closed
    storage.record_source_fetches(
        [(source, label) for source, label, status, _, _ in source_runs if status == "ok"], fetch_start
    )
    storage.record_source_runs(source_runs, fetch_start, disabled)
    storage.compact()
    log.info("Done. %s", stats)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    log = logging.getLogger("run")

    from pathlib import Path
    from starlette.testclient import TestClient
    from job_radar.api.main import create_app

    if not cfg.DB_PATH.exists():
        log.error("jobs.db not found at %s — run 'python run.py fetch' first", cfg.DB_PATH)
        return 1

    # Switched-off sources' roles are hidden on the live site too
    app = create_app(cfg.DB_PATH, threshold=0, disabled_sources_path=cfg.SOURCES_DISABLED_PATH)
    client = TestClient(app)
    response = client.get("/?export=1")

    import shutil

    out = Path("public")
    out.mkdir(exist_ok=True)

    # Make asset paths relative so the file works without a server
    html = response.text.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    (out / "index.html").write_text(html, encoding="utf-8")

    static_src = Path("job_radar/api/static")
    if static_src.exists():
        shutil.copytree(static_src, out / "static", dirs_exist_ok=True)

    icons_src = Path("icons")
    if icons_src.exists():
        shutil.copytree(icons_src, out / "icons", dirs_exist_ok=True)

    log.info("Exported dashboard → public/index.html")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    log = logging.getLogger("run")

    import asyncio
    from job_radar.leads import places
    from job_radar.leads.enrich import CompanyLookup
    from job_radar.leads.runner import detect_all, enrich_existing, link_existing

    profile = cfg.load_profile()
    job_storage = Storage(cfg.DB_PATH)
    leads_storage = Storage(cfg.LEADS_DB_PATH)

    lookup = None
    if cfg.ANTHROPIC_API_KEY:
        lookup = CompanyLookup(cfg.ANTHROPIC_API_KEY, cfg.LEAD_LOOKUP_MODEL)
    else:
        log.warning("ANTHROPIC_API_KEY not set — leads will keep placeholder locations")

    if args.backfill:
        if not lookup:
            log.error("--backfill needs ANTHROPIC_API_KEY")
            return 1
        log.info("Looking up HQ and sector for existing leads...")
        n = enrich_existing(leads_storage, profile, lookup)
        log.info("Enriched %d leads. Looking for websites and careers pages...", n)
        found = asyncio.run(link_existing(leads_storage))
        log.info("Found %d careers pages.", found)
        places.resolve([l["location"] for l in leads_storage.get_leads()], cfg.LEAD_PLACES_PATH)
        log.info("Done.")
        return 0

    log.info("Running lead detection...")
    stats = asyncio.run(
        detect_all(leads_storage, profile, job_storage=job_storage, lookup=lookup)
    )
    # Every lead's location, not just new ones': backfilled leads gain one too
    places.resolve([l["location"] for l in leads_storage.get_leads()], cfg.LEAD_PLACES_PATH)
    log.info("Done. %s", stats)
    return 0


def local_app():
    """The dashboard as `serve` runs it. A function rather than an app, so the
    reloader can build a fresh one each time the code changes."""
    from job_radar.api.main import create_app

    return create_app(
        cfg.DB_PATH,
        threshold=0,
        feedback_path=cfg.FEEDBACK_PATH,
        leads_db_path=cfg.LEADS_DB_PATH,
        profile_path=cfg.PROFILE_PATH,
        locations_path=cfg.LOCATIONS_CACHE_PATH,
        lead_places_path=cfg.LEAD_PLACES_PATH,
        local_fetch=cfg.LOCAL_FETCH,
        disabled_sources_path=cfg.SOURCES_DISABLED_PATH,
    )


def cmd_serve(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    import uvicorn

    # Restarts by itself whenever a .py file changes, so a page never talks to
    # a server still running older code (its saves fail with a 404).
    # Templates and CSS are read fresh on every request anyway.
    uvicorn.run(
        "run:local_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
        reload=True,
        reload_dirs=[str(cfg.ROOT)],
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="job_radar")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Pull jobs and score them")
    p_fetch.add_argument(
        "--force-rescore",
        action="store_true",
        help="Re-score all fetched jobs, ignoring the cache. Use after editing profile.json.",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_detect = sub.add_parser("detect", help="Diff VC portfolio pages and surface new leads")
    p_detect.add_argument(
        "--backfill",
        action="store_true",
        help="Instead of detecting, fill in HQ city, sector, website and careers page "
        "for existing leads that lack them.",
    )
    p_detect.set_defaults(func=cmd_detect)

    p_export = sub.add_parser("export", help="Render the dashboard to public/index.html")
    p_export.set_defaults(func=cmd_export)

    p_serve = sub.add_parser("serve", help="Start the dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
