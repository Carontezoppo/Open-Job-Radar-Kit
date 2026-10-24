"""
SQLite storage. Plain sqlite3 (no ORM) so the SQL is visible and learnable.

Two tables:
  jobs    — every job we've ever seen, with when a fetch last found it
  scores  — Claude's verdict per job (latest wins)

source_fetches records when each source last returned jobs, which is what
makes a missing job meaningful: see top_scored(). source_runs records what
every source did on the latest run (ok / empty / error), for the Sources page.

Feedback lives in feedback.json (see feedback.py), not here.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .models import Job, Lead, Score


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_label  TEXT,
    company       TEXT,
    title         TEXT,
    location      TEXT,
    remote        INTEGER DEFAULT 0,
    description   TEXT,
    url           TEXT,
    posted_at     TEXT,
    discovered_at TEXT,
    raw           TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source     ON jobs(source);

CREATE TABLE IF NOT EXISTS scores (
    job_id      TEXT PRIMARY KEY,
    score       INTEGER NOT NULL,
    verdict     TEXT NOT NULL,
    reasoning   TEXT,
    green_flags TEXT,
    red_flags   TEXT,
    model       TEXT,
    scored_at   TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_scores_score ON scores(score DESC);

CREATE TABLE IF NOT EXISTS leads (
    id            TEXT PRIMARY KEY,
    company_name  TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_detail TEXT DEFAULT '',
    detected_at   TEXT NOT NULL,
    funding_stage TEXT DEFAULT 'unknown',
    vc            TEXT DEFAULT '',
    sector_guess  TEXT DEFAULT '',
    location      TEXT DEFAULT 'unknown',
    founders      TEXT DEFAULT '[]',
    url           TEXT DEFAULT '',
    fit_score     INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'new',
    notes         TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_fit_score ON leads(fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_detected  ON leads(detected_at DESC);

-- When each source (one scraper: e.g. greenhouse + a company's board) last
-- returned jobs. A job its source has stopped listing is closed.
CREATE TABLE IF NOT EXISTS source_fetches (
    source          TEXT NOT NULL,
    source_label    TEXT NOT NULL,
    last_success_at TEXT NOT NULL,
    PRIMARY KEY (source, source_label)
);

-- What each source did on its latest run, for the Sources audit page.
-- status: ok (returned jobs) | empty (returned none) | error (raised)
CREATE TABLE IF NOT EXISTS source_runs (
    source       TEXT NOT NULL,
    source_label TEXT NOT NULL,
    run_at       TEXT NOT NULL,
    status       TEXT NOT NULL,
    job_count    INTEGER NOT NULL,
    error        TEXT,
    PRIMARY KEY (source, source_label)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    vc          TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    companies   TEXT NOT NULL,
    PRIMARY KEY (vc, snapshot_at)
);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)
            try:
                c.execute("ALTER TABLE scores ADD COLUMN previous_score INTEGER")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:
                c.execute("ALTER TABLE leads ADD COLUMN careers_url TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:
                c.execute("ALTER TABLE jobs ADD COLUMN last_seen_at TEXT")
                # Existing jobs were last confirmed when discovered. The next
                # fetch re-confirms the ones still listed; the rest age out
                # as closed.
                c.execute("UPDATE jobs SET last_seen_at = discovered_at")
            except sqlite3.OperationalError:
                pass  # column already exists

    # --- Metadata ---------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- Jobs -------------------------------------------------------------
    def upsert_job(self, job: Job, seen_at: datetime) -> None:
        """Insert or refresh a fetched job; `seen_at` records that its source still lists it."""
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO jobs
                  (id, source, source_id, source_label, company, title, location,
                   remote, description, url, posted_at, discovered_at, raw, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  title = excluded.title,
                  location = excluded.location,
                  description = excluded.description,
                  url = excluded.url,
                  last_seen_at = excluded.last_seen_at
                """,
                (
                    job.id, job.source, job.source_id, job.source_label,
                    job.company, job.title, job.location,
                    int(job.remote), job.description, job.url,
                    job.posted_at.isoformat() if job.posted_at else None,
                    job.discovered_at.isoformat(),
                    # The source's full payload isn't stored: nothing reads it,
                    # and it was over two thirds of the file (see compact()).
                    None,
                    seen_at.isoformat(),
                ),
            )

    def record_source_fetches(self, sources: list[tuple[str, str]], at: datetime) -> None:
        """Note that these (source, label) pairs returned jobs in the fetch started at `at`."""
        with self._conn() as c:
            c.executemany(
                "INSERT INTO source_fetches (source, source_label, last_success_at) VALUES (?, ?, ?)"
                " ON CONFLICT(source, source_label) DO UPDATE SET last_success_at = excluded.last_success_at",
                [(source, label, at.isoformat()) for source, label in sources],
            )

    def compact(self) -> None:
        """Drop data nothing reads, then shrink the file.

        jobs.db is committed to Git LFS on every CI run, and every version
        counts against the LFS storage quota for good, so its size matters.
        Descriptions are only needed to score a job, and scoring always uses
        the freshly fetched copy, so only scored jobs keep theirs (the record
        of what was judged). Unscored jobs keep everything else, since their
        ids drive deduplication.
        """
        with self._conn() as c:
            c.execute("UPDATE jobs SET raw = NULL WHERE raw IS NOT NULL")
            c.execute(
                "UPDATE jobs SET description = NULL"
                " WHERE description IS NOT NULL"
                " AND id NOT IN (SELECT job_id FROM scores)"
            )
        # VACUUM can't run inside a transaction, so it gets its own connection
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    def known_job_ids(self) -> set[str]:
        with self._conn() as c:
            return {row["id"] for row in c.execute("SELECT id FROM jobs")}

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return _row_to_job(row) if row else None

    # --- Scores -----------------------------------------------------------
    def upsert_score(self, score: Score) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO scores
                  (job_id, score, verdict, reasoning, green_flags, red_flags, model, scored_at, previous_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(job_id) DO UPDATE SET
                  previous_score = scores.score,
                  score = excluded.score,
                  verdict = excluded.verdict,
                  reasoning = excluded.reasoning,
                  green_flags = excluded.green_flags,
                  red_flags = excluded.red_flags,
                  model = excluded.model,
                  scored_at = excluded.scored_at
                """,
                (
                    score.job_id, score.score, score.verdict, score.reasoning,
                    json.dumps(score.green_flags), json.dumps(score.red_flags),
                    score.model, score.scored_at.isoformat(),
                ),
            )

    def already_scored(self) -> set[str]:
        with self._conn() as c:
            return {row["job_id"] for row in c.execute("SELECT job_id FROM scores")}

    def record_source_runs(self, runs: list[tuple[str, str, str, int, str | None]], at: datetime) -> None:
        """Store each source's outcome on this run: (source, label, status, job_count, error)."""
        with self._conn() as c:
            c.executemany(
                "INSERT INTO source_runs (source, source_label, run_at, status, job_count, error)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(source, source_label) DO UPDATE SET"
                "   run_at = excluded.run_at, status = excluded.status,"
                "   job_count = excluded.job_count, error = excluded.error",
                [(s, label, at.isoformat(), status, count, error) for s, label, status, count, error in runs],
            )

    def source_report(self) -> list[dict]:
        """Every source in the latest run, with when it last returned jobs and its newest posting.

        Sources missing from the latest run have been removed from the config,
        so they're left out.
        """
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT r.source, r.source_label, r.run_at, r.status, r.job_count, r.error,
                       f.last_success_at,
                       (SELECT MAX(COALESCE(j.posted_at, j.discovered_at)) FROM jobs j
                         WHERE j.source = r.source AND j.source_label = r.source_label) AS newest_posting
                FROM source_runs r
                LEFT JOIN source_fetches f
                  ON f.source = r.source AND f.source_label = r.source_label
                WHERE r.run_at = (SELECT MAX(run_at) FROM source_runs)
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def top_scored(self, limit: int = 50, min_score: int = 0) -> list[dict]:
        """Return jobs+scores joined, sorted by score desc. Used by dashboard.

        Each row carries `source_fetched_at`, when its source last returned
        jobs: a job last seen well before then is no longer listed.
        """
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT j.*, s.score, s.verdict, s.reasoning,
                       s.green_flags, s.red_flags, s.scored_at, s.previous_score,
                       f.last_success_at AS source_fetched_at
                FROM jobs j
                JOIN scores s ON s.job_id = j.id
                LEFT JOIN source_fetches f
                  ON f.source = j.source AND f.source_label = j.source_label
                WHERE s.score >= ?
                ORDER BY s.score DESC, j.discovered_at DESC
                LIMIT ?
                """,
                (min_score, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_score(self, job_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM scores WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def all_job_companies(self) -> set[str]:
        with self._conn() as c:
            return {row["company"].lower().strip() for row in c.execute("SELECT company FROM jobs")}

    # --- Leads -----------------------------------------------------------
    def upsert_lead(self, lead: Lead) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO leads
                  (id, company_name, source, source_detail, detected_at,
                   funding_stage, vc, sector_guess, location, founders,
                   url, careers_url, fit_score, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    lead.id, lead.company_name, lead.source, lead.source_detail,
                    lead.detected_at.isoformat(), lead.funding_stage, lead.vc,
                    lead.sector_guess, lead.location,
                    json.dumps(lead.founders), lead.url, lead.careers_url, lead.fit_score,
                    lead.status, lead.notes,
                ),
            )

    def known_lead_ids(self) -> set[str]:
        with self._conn() as c:
            return {row["id"] for row in c.execute("SELECT id FROM leads")}

    def get_lead(self, lead_id: str) -> Optional[Lead]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
            return _row_to_lead(row) if row else None

    def get_leads(self, status: Optional[str] = None) -> list[dict]:
        with self._conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM leads WHERE status = ? ORDER BY fit_score DESC, detected_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM leads ORDER BY fit_score DESC, detected_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_lead_status(self, lead_id: str, status: str, notes: Optional[str] = None) -> None:
        with self._conn() as c:
            if notes is not None:
                c.execute(
                    "UPDATE leads SET status = ?, notes = ? WHERE id = ?",
                    (status, notes, lead_id),
                )
            else:
                c.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))

    def update_lead_notes(self, lead_id: str, notes: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE leads SET notes = ? WHERE id = ?", (notes, lead_id))

    def update_lead_url(self, lead_id: str, url: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE leads SET url = ? WHERE id = ?", (url, lead_id))

    def update_lead_enrichment(
        self, lead_id: str, location: str, sector_guess: str, fit_score: int
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE leads SET location = ?, sector_guess = ?, fit_score = ? WHERE id = ?",
                (location, sector_guess, fit_score, lead_id),
            )

    def update_lead_links(self, lead_id: str, url: str, careers_url: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE leads SET url = ?, careers_url = ? WHERE id = ?",
                (url, careers_url, lead_id),
            )

    # --- Portfolio snapshots ---------------------------------------------
    def save_snapshot(self, vc: str, companies: list[dict]) -> None:
        from datetime import datetime, timezone
        snapshot_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO portfolio_snapshots (vc, snapshot_at, companies) VALUES (?, ?, ?)",
                (vc, snapshot_at, json.dumps(companies)),
            )

    def get_latest_snapshot(self, vc: str) -> list[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT companies FROM portfolio_snapshots WHERE vc = ?"
                " ORDER BY snapshot_at DESC LIMIT 1",
                (vc,),
            ).fetchone()
            return json.loads(row["companies"]) if row else []



def _row_to_lead(row: sqlite3.Row) -> Lead:
    from datetime import datetime
    return Lead(
        id=row["id"],
        company_name=row["company_name"],
        source=row["source"],
        source_detail=row["source_detail"] or "",
        detected_at=datetime.fromisoformat(row["detected_at"]),
        funding_stage=row["funding_stage"] or "unknown",
        vc=row["vc"] or "",
        sector_guess=row["sector_guess"] or "",
        location=row["location"] or "unknown",
        founders=json.loads(row["founders"]) if row["founders"] else [],
        url=row["url"] or "",
        careers_url=row["careers_url"] or "",
        fit_score=row["fit_score"] or 0,
        status=row["status"] or "new",
        notes=row["notes"] or "",
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    from datetime import datetime
    return Job(
        id=row["id"],
        source=row["source"],
        source_id=row["source_id"],
        source_label=row["source_label"] or "",
        company=row["company"] or "",
        title=row["title"] or "",
        location=row["location"],
        remote=bool(row["remote"]),
        description=row["description"] or "",
        url=row["url"] or "",
        posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        discovered_at=datetime.fromisoformat(row["discovered_at"]),
        raw=json.loads(row["raw"]) if row["raw"] else {},
    )
