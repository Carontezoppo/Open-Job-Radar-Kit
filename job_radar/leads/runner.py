"""
Lead detection orchestrator.

  detect_all(storage, profile) -> stats dict

For each portfolio scraper:
  1. Fetch current company list from the VC's portfolio page.
  2. Diff against the previous snapshot persisted in storage.
  3. Create a Lead for each new company: follow the VC's detail page to the
     company website and its careers page (careers.py), then ask Claude for
     its HQ city and, if the page gave none, its sector (enrich.py).
  4. Save a fresh snapshot so next run has a baseline.

After all scrapers finish, promote any lead whose company has already
appeared in the jobs table (i.e. the aggregated feed caught them first).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic
import httpx

from ..models import Lead
from ..storage import Storage
from .base import BasePortfolioScraper, CompanyEntry
from .careers import find_careers
from .enrich import CompanyFacts, CompanyLookup
from .heuristic import compute_fit_score
from .portfolio import PORTFOLIO_SCRAPERS

logger = logging.getLogger(__name__)


async def detect_all(
    storage: Storage,
    profile: dict[str, Any],
    job_storage: Storage | None = None,
    lookup: CompanyLookup | None = None,
) -> dict[str, int]:
    """
    storage     — leads DB (leads.db); owns the leads table.
    job_storage — jobs DB (jobs.db); used only to cross-reference company names
                  when promoting leads. Falls back to storage if not provided.
    lookup      — Claude company lookup; None skips it (e.g. no API key), and
                  leads keep whatever location the scraper gave them.
    """
    stats: dict[str, int] = {"new_leads": 0, "promoted": 0, "scrapers_ok": 0, "scrapers_failed": 0}

    tasks = [_detect_one(s, storage, profile, stats, lookup) for s in PORTFOLIO_SCRAPERS]
    await asyncio.gather(*tasks, return_exceptions=True)

    stats["promoted"] = _promote_by_job_match(storage, job_storage or storage)

    from datetime import datetime, timezone
    storage.set_meta("last_detect_at", datetime.now(timezone.utc).isoformat())

    return stats


async def _detect_one(
    scraper: BasePortfolioScraper,
    storage: Storage,
    profile: dict[str, Any],
    stats: dict[str, int],
    lookup: CompanyLookup | None,
) -> None:
    try:
        companies = await scraper.fetch()
        logger.info("[%s] fetched %d portfolio companies", scraper.vc_name, len(companies))

        if not companies:
            logger.warning("[%s] returned 0 companies — parser may need calibration", scraper.vc_name)
            stats["scrapers_failed"] += 1
            return

        stats["scrapers_ok"] += 1

        prev_snapshot = storage.get_latest_snapshot(scraper.vc_name)
        prev_names = {c["name"].lower().strip() for c in prev_snapshot}

        new_companies = [c for c in companies if c.name.lower().strip() not in prev_names]
        logger.info("[%s] %d new since last snapshot", scraper.vc_name, len(new_companies))

        known_lead_ids = storage.known_lead_ids()
        profile_sectors = profile.get("sectors", {})

        async with scraper._client() as client:
            for entry in new_companies:
                lead_id = Lead.make_id("portfolio_diff", entry.name)
                if lead_id in known_lead_ids:
                    continue
                website, careers = await _find_links(client, scraper, entry.url)
                # The Claude call is sync; a thread keeps it from stalling the
                # other scrapers' requests on the event loop.
                lead = await asyncio.to_thread(
                    _entry_to_lead, entry, scraper, profile_sectors, lookup, website
                )
                lead.careers_url = careers
                storage.upsert_lead(lead)
                stats["new_leads"] += 1
                logger.info("New lead: %s (fit=%d)", entry.name, lead.fit_score)

        storage.save_snapshot(
            scraper.vc_name,
            [
                {
                    "name": c.name,
                    "url": c.url,
                    "blurb": c.blurb,
                    "location": c.location,
                    "stage": c.stage,
                    "sectors": c.sectors,
                }
                for c in companies
            ],
        )

    except Exception as e:
        logger.error("[%s] detect failed: %s", scraper.vc_name, e)
        stats["scrapers_failed"] += 1


def _entry_to_lead(
    entry: CompanyEntry,
    scraper: BasePortfolioScraper,
    profile_sectors: dict,
    lookup: CompanyLookup | None,
    website: str,
) -> Lead:
    vc_name = scraper.vc_name
    sectors = entry.sectors
    # A scraper's default_location is a placeholder, not something the page
    # said, so it's dropped — "unknown" is more honest than a guess.
    listed = "" if entry.location == scraper.default_location else entry.location
    location = listed

    # Claude's answers win over the page's: its HQ is a city where pages
    # mostly give a country, and its sectors use one vocabulary across every
    # VC (each portfolio page words sectors differently, so "Financial
    # Services" would never match "fintech" in the profile). The page's
    # values still go in as hints, and remain the fallback.
    if lookup:
        facts = _lookup(lookup, entry.name, vc_name, website, listed, ", ".join(sectors))
        if facts:
            location = facts.headquarters or listed
            sectors = facts.sectors or sectors

    sector_guess = ", ".join(sectors) if sectors else ""
    fit = compute_fit_score(
        funding_stage=entry.stage,
        vc=vc_name,
        location=location,
        sector_guess=sector_guess,
        profile_sectors=profile_sectors,
    )
    lead_id = Lead.make_id("portfolio_diff", entry.name)
    return Lead(
        id=lead_id,
        company_name=entry.name,
        source="portfolio_diff",
        source_detail=vc_name,
        funding_stage=entry.stage,
        vc=vc_name,
        sector_guess=sector_guess,
        location=location or "unknown",
        url=website,
        fit_score=fit,
        status="new",
        notes=entry.blurb,
    )


def enrich_existing(
    storage: Storage, profile: dict[str, Any], lookup: CompanyLookup
) -> int:
    """
    Re-run the company lookup for leads still carrying a placeholder location
    or no sector, and recompute their fit score. Mainly a one-off backfill for
    leads detected before the lookup existed; safe to re-run.
    """
    placeholders = {s.vc_name: s.default_location for s in PORTFOLIO_SCRAPERS}
    profile_sectors = profile.get("sectors", {})
    count = 0
    for lead in storage.get_leads():
        if lead["source"] != "portfolio_diff":
            continue
        placeholder = placeholders.get(lead["vc"])
        location = lead["location"]
        sector_guess = lead["sector_guess"]
        needs_location = location in (placeholder, "unknown")
        if not needs_location and sector_guess:
            continue

        facts = _lookup(lookup, lead["company_name"], lead["vc"], lead["url"], "", "")
        if not facts:
            continue
        if needs_location:
            location = facts.headquarters or "unknown"
        if not sector_guess:
            sector_guess = ", ".join(facts.sectors)

        fit = compute_fit_score(
            funding_stage=lead["funding_stage"],
            vc=lead["vc"],
            location=location,
            sector_guess=sector_guess,
            profile_sectors=profile_sectors,
        )
        storage.update_lead_enrichment(lead["id"], location, sector_guess, fit)
        logger.info("Enriched %s: %s [%s] fit %d→%d",
                    lead["company_name"], location, sector_guess, lead["fit_score"], fit)
        count += 1
    return count


async def link_existing(storage: Storage) -> int:
    """
    Find website and careers page for leads that have no careers link yet.
    Mainly a one-off backfill for leads detected before careers.py existed;
    safe to re-run (a lead whose url is already the company site keeps it).
    """
    scrapers = {s.vc_name: s for s in PORTFOLIO_SCRAPERS}
    leads = [
        l for l in storage.get_leads()
        if l["source"] == "portfolio_diff" and not l["careers_url"] and l["vc"] in scrapers
    ]
    found = 0
    sem = asyncio.Semaphore(8)  # polite to the VC sites, still ~8x faster

    async def one(lead: dict, client: httpx.AsyncClient) -> None:
        nonlocal found
        async with sem:
            website, careers = await _find_links(client, scrapers[lead["vc"]], lead["url"])
        storage.update_lead_links(lead["id"], website, careers)
        if careers:
            found += 1
            logger.info("Careers for %s: %s", lead["company_name"], careers)

    async with scrapers[next(iter(scrapers))]._client() as client:
        await asyncio.gather(*(one(l, client) for l in leads))
    return found


async def _find_links(
    client: httpx.AsyncClient, scraper: BasePortfolioScraper, url: str
) -> tuple[str, str]:
    """(website, careers_url). Falls back to the given url and no careers link."""
    if not url:
        return "", ""
    try:
        website = await scraper.website_for(client, url)
    except httpx.HTTPError as e:
        logger.debug("website lookup failed for %s: %s", url, e)
        return url, ""
    if not website.startswith("http"):
        website = url
    return website, await find_careers(client, website)


def _lookup(
    lookup: CompanyLookup, name: str, vc: str, url: str,
    listed_location: str, listed_sectors: str,
) -> CompanyFacts | None:
    """A failed lookup shouldn't cost us the lead — log it and carry on."""
    try:
        return lookup.lookup(name, vc, url, listed_location, listed_sectors)
    except anthropic.APIError as e:
        logger.warning("Company lookup failed for %s: %s", name, e)
        return None


def _promote_by_job_match(leads_storage: Storage, job_storage: Storage) -> int:
    """Promote leads whose company name has already appeared in the jobs table."""
    job_companies = job_storage.all_job_companies()
    active_leads = [
        l for l in leads_storage.get_leads() if l["status"] in ("new", "watching")
    ]
    count = 0
    for lead in active_leads:
        name = lead["company_name"].lower().strip()
        if name in job_companies:
            leads_storage.update_lead_status(lead["id"], "promoted")
            logger.info("Promoted lead: %s (jobs feed matched)", lead["company_name"])
            count += 1
    return count
