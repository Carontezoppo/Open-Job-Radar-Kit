"""
The canonical Job model. Every scraper normalises into this shape.

Why Pydantic: catches malformed data early, and gives you free JSON
serialisation when storing to SQLite or sending to a template.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    """A single job posting, normalised across sources."""

    # Stable ID derived from source + source_id so duplicates collapse
    id: str

    source: str  # 'greenhouse' | 'lever' | 'getro' | ...
    source_id: str  # the original ID from that source
    source_label: str = ""  # human-readable e.g. "Index Ventures"

    company: str
    title: str
    location: Optional[str] = None
    remote: bool = False

    description: str = ""  # plain text, HTML stripped
    url: str  # apply / view URL

    posted_at: Optional[datetime] = None
    discovered_at: datetime = Field(default_factory=_utcnow)

    # Original payload, useful for debugging and reprocessing without re-fetching
    raw: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def make_id(source: str, source_id: str) -> str:
        """Stable, short ID used as primary key in SQLite."""
        h = hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()
        return h[:16]


class Score(BaseModel):
    """Claude's verdict on a single job."""

    job_id: str
    score: int  # 0–100
    verdict: str  # 'strong_match' | 'possible_match' | 'weak_match' | 'skip'
    reasoning: str
    green_flags: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    model: str
    scored_at: datetime = Field(default_factory=_utcnow)


class Feedback(BaseModel):
    """User thumbs feedback. Used to tune the prompt over time."""

    job_id: str
    rating: Optional[str] = None  # 'up' | 'down'
    hidden: Optional[bool] = None  # None = no change to existing state
    applied: Optional[bool] = None  # None = no change to existing state
    positive: str = ""
    negative: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class Lead(BaseModel):
    """A company that may need a designer soon, before any role is posted."""

    id: str
    company_name: str
    source: str  # 'portfolio_diff' | 'funding_feed' | 'accelerator' | 'manual'
    source_detail: str = ""
    detected_at: datetime = Field(default_factory=_utcnow)
    funding_stage: str = "unknown"  # 'pre-seed' | 'seed' | 'unknown' | ...
    vc: str = ""
    sector_guess: str = ""
    location: str = "unknown"
    founders: list[str] = Field(default_factory=list)
    url: str = ""  # company website, falling back to the VC's page for it
    careers_url: str = ""
    fit_score: int = 0
    status: str = "new"  # 'new' | 'watching' | 'dismissed' | 'promoted'
    notes: str = ""

    @staticmethod
    def make_id(source: str, company_name: str) -> str:
        h = hashlib.sha256(f"{source}:{company_name.lower().strip()}".encode()).hexdigest()
        return h[:16]


class ProfileLocation(BaseModel):
    # Unknown keys (home, would_relocate, would_not_relocate) are kept as-is,
    # so saving from the settings panel never drops a field it doesn't show.
    model_config = ConfigDict(extra="allow")

    countries: list[str] = []
    remote_ok: bool = True


class ProfileSectors(BaseModel):
    strong_interest: list[str] = []
    open_to: list[str] = []
    avoid: list[str] = []


class Profile(BaseModel):
    """Shape of profile.json, used to check saves and uploads before they're written.

    The pipeline still reads the file as a plain dict (config.load_profile);
    this only stops a malformed file reaching it.
    """

    model_config = ConfigDict(extra="allow")

    name: str = ""
    headline: str = ""
    summary: str = ""
    role_families_open_to: list[str] = []
    role_families_not_interested: list[str] = []
    location: ProfileLocation = Field(default_factory=ProfileLocation)
    sectors: ProfileSectors = Field(default_factory=ProfileSectors)
    green_flags: list[str] = []
    red_flags: list[str] = []
    must_have_keywords: list[str] = []
    must_not_have_keywords: list[str] = []
