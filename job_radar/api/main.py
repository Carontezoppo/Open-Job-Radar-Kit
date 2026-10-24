"""
FastAPI application: serves the dashboard and accepts thumbs feedback.

Run via: python run.py serve
Then open: http://localhost:8000
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from .. import feedback as fb_store
from .. import geo
from ..leads import places as lead_places
from ..models import Feedback, Lead, Profile
from ..storage import Storage
from .fetch_runner import FetchRunner

NEW_PILL_DAYS = 3
# The list shows roles posted in the last month: older postings are usually
# filled, or were never real. The window, not a count, keeps the list short.
LIST_MAX_AGE_DAYS = 30
# Skips are kept for the Skip chip only above this score. Below it they are
# clearly off-profile (engineering, finance, ops) and would bloat the page.
SKIP_MIN_SCORE = 20
# A job counts as closed once its source has kept returning other jobs for
# this long without it: about two fetches, so one flaky page isn't enough.
CLOSED_AFTER_HOURS = 24


def _is_closed(row: dict) -> bool:
    """True if the job's source has stopped listing it (see CLOSED_AFTER_HOURS)."""
    if not row["source_fetched_at"] or not row["last_seen_at"]:
        return False  # source never fetched successfully since tracking began
    gap = datetime.fromisoformat(row["source_fetched_at"]) - datetime.fromisoformat(row["last_seen_at"])
    return gap > timedelta(hours=CLOSED_AFTER_HOURS)


HERE = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))


class FeedbackIn(BaseModel):
    job_id: str
    rating: str | None = None  # 'up' | 'down'
    hidden: bool | None = None
    applied: bool | None = None
    positive: str = ""
    negative: str = ""


class FetchIn(BaseModel):
    force_rescore: bool = False


class LeadActionIn(BaseModel):
    lead_id: str
    status: str | None = None  # 'watching' | 'dismissed' | 'promoted'
    notes: str | None = None
    url: str | None = None


class ManualLeadIn(BaseModel):
    company_name: str
    url: str = ""
    source_detail: str = ""
    funding_stage: str = "unknown"
    vc: str = ""
    sector_guess: str = ""
    location: str = "unknown"
    notes: str = ""


def create_app(
    db_path: Path,
    threshold: int = 0,
    feedback_path: Path | None = None,
    leads_db_path: Path | None = None,
    profile_path: Path | None = None,
    locations_path: Path | None = None,
    lead_places_path: Path | None = None,
    local_fetch: bool = False,
) -> FastAPI:
    """Factory so we can inject the db path from run.py.

    `profile_path` turns on the settings panel and the country filter. The
    static export leaves it out, so the live site is unaffected by both.
    `local_fetch` adds the Fetch button (see config.LOCAL_FETCH).
    """
    app = FastAPI(title="Job Radar")
    storage = Storage(db_path)
    leads_storage = Storage(leads_db_path) if leads_db_path else storage

    static_dir = HERE / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    icons_dir = HERE.parent.parent / "icons"
    if icons_dir.exists():
        app.mount("/icons", StaticFiles(directory=str(icons_dir)), name="icons")

    def read_profile() -> dict[str, Any]:
        # Same fallback as config.load_profile: a fresh copy has only the example.
        path = profile_path if profile_path.exists() else profile_path.with_name("profile.example.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, export: bool = Query(False)) -> Any:
        allowed = geo.allowed_countries(read_profile()) if profile_path else None
        # Hide scored jobs outside the chosen countries, so changing them
        # takes effect at once rather than waiting for a rescore.
        cache = geo.load_cache(locations_path) if allowed is not None and locations_path else {}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=LIST_MAX_AGE_DAYS)).isoformat()
        feedback_map = fb_store.latest_by_job(feedback_path) if feedback_path else {}
        rows = []
        for r in storage.top_scored(limit=-1, min_score=threshold):
            fb = feedback_map.get(r["id"]) or {}
            if fb.get("hidden"):
                continue
            r["applied"] = fb.get("applied", False)
            r["closed"] = _is_closed(r)
            recent = (r["posted_at"] or r["discovered_at"] or "") >= cutoff
            if r["applied"]:
                # Applications stay retrievable whatever their age, verdict or
                # status; once closed or past the window, only under Applied
                r["archived"] = r["closed"] or not recent
            else:
                if r["closed"] or not recent:
                    continue
                if r["verdict"] == "skip" and r["score"] < SKIP_MIN_SCORE:
                    continue
                if allowed is not None and not geo.in_allowed(cache.get((r["location"] or "").strip()), allowed):
                    continue
                r["archived"] = False
            r["rating"] = fb.get("rating")
            r["positive"] = fb.get("positive")
            r["negative"] = fb.get("negative")
            r["green_flags"] = json.loads(r["green_flags"]) if r["green_flags"] else []
            r["red_flags"] = json.loads(r["red_flags"]) if r["red_flags"] else []
            rows.append(r)
        summary = fb_store.summary(feedback_path) if feedback_path else {"up": 0, "down": 0}
        # A role stays "New" for three days after it's scored. Tying it to the
        # last fetch instead meant the pill vanished on the next run (twice a
        # day), usually before anyone had looked.
        new_since = (datetime.now(timezone.utc) - timedelta(days=NEW_PILL_DAYS)).isoformat()
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "jobs": rows,
                "summary": summary,
                # Skips and archived applications sit behind their own chips, so
                # they aren't "ranked" roles (a skip voted Interested is)
                "total": sum(
                    1 for r in rows
                    if not r["archived"] and (r["verdict"] != "skip" or r["rating"] == "up")
                ),
                "new_since": new_since,
                "is_static": export,
                "settings": bool(profile_path) and not export,
                "local_fetch": local_fetch and not export,
            },
        )

    @app.post("/api/feedback")
    async def post_feedback(payload: FeedbackIn) -> Any:
        if payload.rating is not None and payload.rating not in ("up", "down"):
            return JSONResponse({"ok": False, "error": "rating must be up|down"}, status_code=400)
        if not feedback_path:
            return JSONResponse({"ok": False, "error": "feedback_path not configured"}, status_code=500)
        fb_store.add(feedback_path, Feedback(
            job_id=payload.job_id,
            rating=payload.rating,
            hidden=payload.hidden,
            applied=payload.applied,
            positive=payload.positive,
            negative=payload.negative,
        ))
        return {"ok": True}

    @app.get("/api/profile")
    async def get_profile() -> Any:
        if not profile_path:
            return JSONResponse({"ok": False, "error": "profile_path not configured"}, status_code=404)
        return {"profile": read_profile(), "regions": geo.REGIONS}

    @app.put("/api/profile")
    async def put_profile(payload: dict[str, Any]) -> Any:
        if not profile_path:
            return JSONResponse({"ok": False, "error": "profile_path not configured"}, status_code=404)
        try:
            profile = Profile.model_validate(payload)
        except ValidationError as e:
            # First problem only, phrased for a person, not a stack trace
            err = e.errors()[0]
            where = ".".join(str(p) for p in err["loc"])
            return JSONResponse({"ok": False, "error": f"{where}: {err['msg']}"}, status_code=400)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile.model_dump(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        return {"ok": True}

    @app.get("/api/profile/download")
    async def download_profile() -> Any:
        if not profile_path or not profile_path.exists():
            return JSONResponse({"ok": False, "error": "no profile.json saved yet"}, status_code=404)
        return FileResponse(profile_path, media_type="application/json", filename="profile.json")

    fetcher = FetchRunner()

    @app.post("/api/fetch")
    async def start_fetch(payload: FetchIn) -> Any:
        if not local_fetch:
            return JSONResponse({"ok": False, "error": "LOCAL_FETCH is not enabled"}, status_code=404)
        if not fetcher.start(payload.force_rescore):
            return JSONResponse({"ok": False, "error": "a fetch is already running"}, status_code=409)
        return {"ok": True}

    @app.get("/api/fetch")
    async def fetch_status() -> Any:
        return fetcher.status()

    @app.get("/api/health")
    async def health() -> Any:
        return {"ok": True}

    @app.get("/leads", response_class=HTMLResponse)
    async def leads_view(request: Request) -> Any:
        leads = leads_storage.get_leads()
        for lead in leads:
            lead["founders"] = json.loads(lead["founders"]) if lead["founders"] else []
        last_detect_at = leads_storage.get_meta("last_detect_at") or ""

        # Map places: location strings that geocode to the same point ("London"
        # and "London, United Kingdom") share one bubble, keyed by rounded coordinates
        cache = lead_places.load(lead_places_path) if lead_places_path else {}
        places: dict[str, dict] = {}
        for lead in leads:
            hit = cache.get((lead["location"] or "").strip())
            if not hit:
                lead["place"] = ""
                continue
            key = f"{hit['lat']:.2f},{hit['lon']:.2f}"
            lead["place"] = key
            # Label from our own wording ("London"), not the geocoder's ("Greater London")
            label = lead["location"].split(",")[0].strip() if hit["kind"] == "city" else hit["name"]
            place = places.setdefault(key, {"lat": hit["lat"], "lon": hit["lon"], "kind": hit["kind"], "label": label})
            if len(label) < len(place["label"]):
                place["label"] = label

        # Open roles per company: what the Roles list would show for it (recent,
        # still listed, not a clear miss), matched by company name
        cutoff = (datetime.now(timezone.utc) - timedelta(days=LIST_MAX_AGE_DAYS)).isoformat()
        roles: dict[str, list[dict]] = {}
        for r in storage.top_scored(limit=-1, min_score=threshold):
            if (r["posted_at"] or r["discovered_at"] or "") < cutoff or _is_closed(r):
                continue
            if r["verdict"] == "skip" and r["score"] < SKIP_MIN_SCORE:
                continue
            roles.setdefault((r["company"] or "").strip().lower(), []).append(
                {"id": r["id"], "title": r["title"], "score": r["score"], "url": r["url"]}
            )
        return TEMPLATES.TemplateResponse(
            request,
            "leads.html",
            {
                "leads": leads,
                "places": places,
                "roles_by_company": roles,
                "total": len(leads),
                "new_count": sum(1 for l in leads if l["status"] == "new"),
                "last_detect_at": last_detect_at,
                "settings": bool(profile_path),
            },
        )

    @app.get("/sources", response_class=HTMLResponse)
    async def sources_view(request: Request) -> Any:
        """Audit of every source's latest fetch. Local only: the export renders "/" alone."""
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(days=LIST_MAX_AGE_DAYS)).isoformat()
        rows = storage.source_report()
        for r in rows:
            ok_at = r["last_success_at"]
            r["days_since_ok"] = (now - datetime.fromisoformat(ok_at)).days if ok_at else None
            r["newest_stale"] = bool(r["newest_posting"]) and r["newest_posting"] < stale_before
        # Problems first — errors, then empty — with sources that never returned
        # anything leading each group, then the longest quiet
        status_order = {"error": 0, "empty": 1, "ok": 2}
        rows.sort(key=lambda r: (
            status_order.get(r["status"], 3),
            r["days_since_ok"] is not None,
            -(r["days_since_ok"] or 0),
            r["source_label"].lower(),
        ))
        return TEMPLATES.TemplateResponse(
            request,
            "sources.html",
            {
                "sources": rows,
                "attention": sum(1 for r in rows if r["status"] != "ok"),
                "last_run_at": rows[0]["run_at"] if rows else None,
                "settings": bool(profile_path),
            },
        )

    @app.post("/api/lead-action")
    async def lead_action(payload: LeadActionIn) -> Any:
        valid_statuses = {"watching", "dismissed", "promoted", "new"}
        if payload.status and payload.status not in valid_statuses:
            return JSONResponse({"ok": False, "error": "invalid status"}, status_code=400)
        if payload.status is not None and payload.notes is not None:
            leads_storage.update_lead_status(payload.lead_id, payload.status, payload.notes)
        elif payload.status is not None:
            leads_storage.update_lead_status(payload.lead_id, payload.status)
        elif payload.notes is not None:
            leads_storage.update_lead_notes(payload.lead_id, payload.notes)
        if payload.url is not None:
            leads_storage.update_lead_url(payload.lead_id, payload.url)
        return {"ok": True}

    @app.post("/api/lead")
    async def create_lead(payload: ManualLeadIn) -> Any:
        if not payload.company_name.strip():
            return JSONResponse({"ok": False, "error": "company_name required"}, status_code=400)
        from ..leads.heuristic import compute_fit_score
        from ..models import Lead as LeadModel
        profile = {}
        try:
            import config as cfg
            profile = cfg.load_profile()
        except Exception:
            pass
        fit = compute_fit_score(
            funding_stage=payload.funding_stage,
            vc=payload.vc,
            location=payload.location,
            sector_guess=payload.sector_guess,
            profile_sectors=profile.get("sectors", {}),
        )
        lead = LeadModel(
            id=LeadModel.make_id("manual", payload.company_name),
            company_name=payload.company_name.strip(),
            source="manual",
            source_detail=payload.source_detail,
            funding_stage=payload.funding_stage,
            vc=payload.vc,
            sector_guess=payload.sector_guess,
            location=payload.location,
            url=payload.url,
            fit_score=fit,
            status="new",
            notes=payload.notes,
        )
        leads_storage.upsert_lead(lead)
        return {"ok": True, "lead_id": lead.id, "fit_score": fit}

    return app
