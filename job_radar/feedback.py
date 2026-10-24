"""
Feedback I/O. feedback.json is the single source of truth for thumbs votes.

The DB has no feedback table. All reads and writes go through this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import Feedback

if TYPE_CHECKING:
    from .storage import Storage


def load(path: Path) -> list[dict]:
    """Return all feedback entries. Empty list if the file doesn't exist."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: Path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add(path: Path, fb: Feedback) -> None:
    """Upsert: merge the new fields into the existing state for this job, then replace."""
    entries = load(path)

    existing: dict = {}
    for e in entries:
        if e["job_id"] == fb.job_id:
            existing = e  # last entry wins, matching latest_by_job behaviour

    new_entry = {
        "job_id": fb.job_id,
        "rating": fb.rating if fb.rating is not None else existing.get("rating"),
        "hidden": fb.hidden if fb.hidden is not None else existing.get("hidden", False),
        "applied": fb.applied if fb.applied is not None else existing.get("applied", False),
        "positive": fb.positive or existing.get("positive") or None,
        "negative": fb.negative or existing.get("negative") or None,
        "created_at": fb.created_at.isoformat(),
    }

    updated = [e for e in entries if e["job_id"] != fb.job_id]
    updated.append(new_entry)
    save(path, updated)


def summary(path: Path) -> dict:
    # Use latest-per-job and exclude hidden entries so the counts match the visible list.
    latest = latest_by_job(path)
    return {
        "up": sum(1 for e in latest.values() if e.get("rating") == "up" and not e.get("hidden")),
        "down": sum(1 for e in latest.values() if e.get("rating") == "down" and not e.get("hidden")),
    }


def latest_by_job(path: Path) -> dict[str, dict]:
    """Return the most recent feedback entry per job_id, keyed by job_id.
    Iterating in order and overwriting is correct because the file is
    appended chronologically."""
    result: dict[str, dict] = {}
    for entry in load(path):
        result[entry["job_id"]] = entry
    return result


def get_examples(path: Path, storage: "Storage", per_rating: int = 4) -> list[dict]:
    """Return up to per_rating up-votes and per_rating down-votes joined
    with job title/company/location and prior score. Deduped by job_id
    (latest vote per job wins)."""
    by_job = latest_by_job(path)

    ups = sorted(
        [e for e in by_job.values() if e["rating"] == "up"],
        key=lambda e: e.get("created_at", ""),
        reverse=True,
    )
    downs = sorted(
        [e for e in by_job.values() if e["rating"] == "down"],
        key=lambda e: e.get("created_at", ""),
        reverse=True,
    )

    results = []
    for entry in ups[:per_rating] + downs[:per_rating]:
        job = storage.get_job(entry["job_id"])
        if not job:
            continue
        score = storage.get_score(entry["job_id"])
        results.append({
            "rating": entry["rating"],
            "positive": entry.get("positive"),
            "negative": entry.get("negative"),
            "created_at": entry.get("created_at"),
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "prior_score": score["score"] if score else None,
        })
    return results
