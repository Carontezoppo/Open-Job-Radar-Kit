"""
Pipeline orchestrator.

  fetch_all() -> for each configured source, instantiate scraper, await fetch()
  process()  -> dedupe vs storage, pre-filter, score with Claude, persist

Every fetched job is stamped with the fetch time (last_seen_at), and each
source that returned jobs is recorded, so the dashboard can tell a job its
source no longer lists.

Run from the CLI via run.py: `python run.py fetch`.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from pathlib import Path
from typing import Any

from . import feedback as fb_store
from . import geo
from .matcher import ClaudeMatcher, passes_prefilter
from .models import Job
from .sources import AshbyScraper, ConsiderScraper, GetroJsonScraper, GetroScraper, GreenhouseScraper, LeverScraper
from .storage import Storage
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


SCRAPER_TYPES = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "getro": GetroScraper,
    "getro_json": GetroJsonScraper,
    "consider": ConsiderScraper,
    "ashby": AshbyScraper,
}


SourceRun = tuple[str, str, str, int, "str | None"]  # (source, label, status, job_count, error)


async def fetch_all(
    sources: list[dict[str, Any]], disabled: set[tuple[str, str]] = frozenset()
) -> tuple[list[Job], list[SourceRun]]:
    """Fan out across all configured sources concurrently and gather results.

    Also returns each scraper's outcome — status "ok" (returned jobs), "empty"
    or "error". Only "ok" counts as a successful look at the board: a failed
    fetch, or an empty one (indistinguishable from a scraper that quietly
    broke), says nothing about whether its jobs are still listed. Some
    scrapers catch their own request errors and return nothing, so they show
    as "empty" rather than "error".

    Sources switched off on the Sources page (`disabled`, as (source, label)
    pairs) are skipped and get no outcome, so their last one stays on record.
    """
    tasks = []
    skipped = 0
    for s in sources:
        s_type = s["type"]
        cls = SCRAPER_TYPES.get(s_type)
        if not cls:
            logger.warning("Unknown source type: %s", s_type)
            continue
        kwargs = {k: v for k, v in s.items() if k != "type"}
        scraper = cls(**kwargs)
        # Checked on the scraper, not the config entry: it fills in a missing label
        if (scraper.source_name, scraper.label) in disabled:
            skipped += 1
            continue
        tasks.append(_fetch_one(scraper))
    if skipped:
        logger.info("Skipping %d switched-off source(s)", skipped)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_jobs: list[Job] = []
    runs: list[SourceRun] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Source failed: %s", r)
            continue
        scraper, jobs, error = r
        all_jobs.extend(jobs)
        status = "error" if error else ("ok" if jobs else "empty")
        runs.append((scraper.source_name, scraper.label, status, len(jobs), error))
    return all_jobs, runs


# A board that times out, hangs up or answers 5xx usually works again
# moments later (Consider's boards did, twice in a day), so it gets one more
# try before it shows as Error on the Sources page.
RETRY_DELAY_SECONDS = 5


async def _fetch_one(scraper) -> tuple[Any, list[Job], str | None]:
    for attempt in (1, 2):
        try:
            jobs = await scraper.fetch()
            logger.info("[%s/%s] fetched %d jobs", scraper.source_name, scraper.label, len(jobs))
            return scraper, jobs, None
        except Exception as e:
            if attempt == 1 and _is_transient(e):
                logger.warning(
                    "[%s/%s] %s, retrying in %ds",
                    scraper.source_name, scraper.label, _describe_error(e), RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)
                continue
            logger.error("[%s/%s] fetch failed: %s", scraper.source_name, scraper.label, _describe_error(e))
            return scraper, [], _describe_error(e)


def _is_transient(e: Exception) -> bool:
    """Failures worth one retry: the board is there but struggling. A 404 or
    a parse error would fail the same way again, so those aren't retried."""
    if isinstance(e, (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectError)):
        return True
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500


def _describe_error(e: Exception) -> str:
    """A short reason for the Sources page, e.g. "404 Not Found · api.lever.co/v0/postings/x"."""
    if isinstance(e, httpx.HTTPStatusError):
        url = str(e.request.url).removeprefix("https://")
        return f"{e.response.status_code} {e.response.reason_phrase} · {url}"
    # First line only, and never empty (some exceptions have no message)
    return (str(e).splitlines() or [type(e).__name__])[0][:300]

def _is_fresh(job: Job, max_age_days: int | None) -> bool:
    """True if the job is within the freshness window.

    Prefers job.posted_at when the source provided it; falls back to
    discovered_at. Missing/unparseable timestamps default to True — better
    to score something marginal than silently drop it on bad metadata.
    """
    if max_age_days is None:
        return True

    ts = job.posted_at or job.discovered_at
    if ts is None:
        return True

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return ts >= cutoff

def process(
    jobs: list[Job],
    profile: dict[str, Any],
    storage: Any,
    matcher: ClaudeMatcher | None,
    max_to_score: int,
    max_age_days: int | None = None,
    force_rescore: bool = False,
    fetch_start: datetime | None = None,
    feedback_path: Path | None = None,
    resolver: geo.LocationResolver | None = None,
) -> dict[str, int]:
    stats = {
        "fetched": len(jobs),
        "new": 0,
        "scored": 0,
        "skipped_prefilter": 0,
        "skipped_stale": 0,
    }

    known = storage.known_job_ids()
    already_scored = storage.already_scored()

    # Always upsert (which also records the job as still listed); count new arrivals
    seen_at = fetch_start or datetime.now(timezone.utc)
    new_jobs: list[Job] = []
    for job in jobs:
        storage.upsert_job(job, seen_at)
        if job.id not in known:
            stats["new"] += 1
            new_jobs.append(job)

    # Pick the candidate pool:
    #   normal mode  → only jobs we haven't scored yet
    #   force mode   → every fetched job, cache ignored
    allowed = geo.allowed_countries(profile)
    countries: dict[str, list[str]] = {}
    if allowed is not None:
        # Every fetched location, not just the pool's: the dashboard filters
        # already-scored jobs from the same cache.
        countries = resolver.resolve([j.location or "" for j in jobs]) if resolver else {}

    if force_rescore:
        logger.info("force_rescore=True — ignoring cached scores")
        pool = jobs
    elif allowed is not None:
        # With a country filter, re-screen every unscored job, not just new
        # ones, so widening the countries scores jobs that were fetched earlier
        # but dropped. Tied to the filter so profiles without it (the live CI
        # profile) keep the original behaviour.
        pool = [j for j in jobs if j.id not in already_scored]
    else:
        pool = [j for j in new_jobs if j.id not in already_scored]

    candidates: list[Job] = []
    for job in pool:
        if not _is_fresh(job, max_age_days):
            logger.debug("Stale (>%s days): %s @ %s", max_age_days, job.title, job.company)
            stats["skipped_stale"] += 1
            continue
        ok, reason = passes_prefilter(job, profile, countries.get((job.location or "").strip()))
        if not ok:
            logger.debug("Pre-filter dropped %s (%s)", job.title, reason)
            stats["skipped_prefilter"] += 1
            continue
        candidates.append(job)

    candidates = candidates[:max_to_score]
    logger.info("Pre-filter passed: %d, will score: %d", len(candidates), len(candidates))

    if not matcher:
        logger.warning("No ANTHROPIC_API_KEY set — skipping AI scoring step")
        return stats
    
    feedback_examples = fb_store.get_examples(feedback_path, storage, per_rating=4) if feedback_path else []
    if feedback_examples:
        logger.info("Calibrating matcher with %d feedback examples", len(feedback_examples))

    for job in candidates:
        try:
            score = matcher.score(job, profile, feedback_examples=feedback_examples)
            storage.upsert_score(score)
            stats["scored"] += 1
            logger.info("Scored [%d %s] %s @ %s", score.score, score.verdict, job.title, job.company)
        except Exception as e:
            logger.error("Score failed for %s: %s", job.title, e)

    if fetch_start is not None:
        storage.set_meta("last_fetch_at", fetch_start.isoformat())

    return stats
