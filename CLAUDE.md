# CLAUDE.md

Context for any AI assistant working on this repository (Claude Code, Cursor, Continue, etc). Read this first.

## What this is

A personal job aggregator. Scrapes VC portfolio boards (Getro-powered and Consider-powered) and ATS sources (Greenhouse, Lever) into SQLite, scores each job against the user's profile via the Anthropic API, and serves a local FastAPI dashboard for review and thumbs feedback.

It is a personal tool, not a product: each copy has one user, who runs it with their own profile.

**Who you're working with** is described in `CLAUDE.local.md` (gitignored, one per copy). If there isn't one, this is a fresh copy: follow the `setup` skill (`.claude/skills/setup/SKILL.md`) before anything else, and write `CLAUDE.local.md` with the person as part of it.

Some of what follows describes the original copy's deployment — GitHub Actions fetching twice a day, `jobs.db` in Git LFS, a live dashboard on Cloudflare Pages. It's all optional: a copy that runs `fetch` on the laptop needs none of it.

## Architecture

```
run.py              — CLI entry: `fetch` (pipeline) | `serve` (FastAPI)
config.py           — Settings, sources list, profile loader
profile.json        — User profile (committed, but CI reads the PROFILE_JSON secret instead; profile.example.json is the template)
locations.json      — Cache of job location string → ISO country codes (gitignored, local only)
lead_places.json    — Cache of lead location string → map coordinates (gitignored, local only)
sources_disabled.json — Sources switched off on the Sources page (committed here so CI honours it; gitignored in the public kit)

job_radar/
├── models.py       — Pydantic models: Job, Score, Feedback, Lead, Profile
├── storage.py      — Plain sqlite3 layer (no ORM). Tables: jobs, scores, metadata, leads, portfolio_snapshots
├── matcher.py      — Two-stage: rule-based pre-filter, then Claude scoring
├── runner.py       — Orchestrator: fetch → dedupe → pre-filter → score → save
├── geo.py          — Location strings → country codes (Haiku, cached); country filter rules
├── sources/        — One file per source. All inherit BaseScraper, return list[Job]
│   ├── base.py
│   ├── greenhouse.py    — Public Greenhouse boards API
│   ├── lever.py         — Public Lever postings API
│   ├── getro.py         — Two classes: GetroJsonScraper (preferred) + GetroScraper (HTML fallback)
│   ├── consider.py      — Consider.com-powered boards (Phoenix Court, Notion Capital, Balderton)
│   └── ashby.py         — Public Ashby JSON API. Largest source by company count.
├── leads/          — Pre-posting leads: companies newly added to VC portfolios
│   ├── runner.py        — detect_all: diff each portfolio against its last snapshot, build leads
│   ├── portfolio/       — One scraper per VC portfolio page, all inherit BasePortfolioScraper
│   ├── enrich.py        — Claude lookup of HQ city + sector (Haiku, structured output)
│   ├── careers.py       — Finds a company's careers page from its website (no LLM)
│   ├── places.py        — Lead locations → map coordinates (OpenStreetMap Nominatim, cached)
│   └── heuristic.py     — Rule-based fit score (stage, VC, location, sector)
└── api/
    ├── main.py     — FastAPI app factory, routes
    ├── fetch_runner.py — Runs `run.py fetch` as a subprocess for the dashboard's Fetch button
    └── templates/
        ├── dashboard.html  — Roles page, editorial styling
        ├── leads.html      — Leads page
        ├── sources.html    — Sources audit page (local only)
        ├── _settings.html  — Profile panel, included by both pages when served locally
        ├── _notify.html    — postJSON + the notification shown when a save fails, included by every page that saves
        └── _icons.html     — Inline SVG icons (Line Awesome, MIT) as Jinja macros; currently just Save
```

The flow is: each source returns normalised `Job` objects → `process()` in runner.py dedupes against storage, runs the pre-filter, sends survivors to Claude, persists scores → dashboard reads from storage.

## Conventions

- **Plain stdlib over heavy frameworks where reasonable.** sqlite3 not SQLAlchemy. argparse not Click. The aim is for someone learning Python to be able to read every line.
- **Pydantic for data shapes only.** Not for config (config.py is plain Python), not for ORM.
- **Async where it pays off.** Source scraping is async (multiple sources fan out concurrently). Everything else is sync — the matcher's Claude calls are deliberately sequential to keep rate-limit and cost behaviour predictable.
- **Top-of-file docstrings explain the module's role in the system.** Function docstrings explain *why* if the function does anything non-obvious.
- **No exception swallowing in the pipeline.** Per-source errors are caught in `runner._fetch_one` and logged; per-job parse errors are caught in each scraper's `fetch` method. Otherwise let exceptions propagate.
- **Logger names match module names** (`logging.getLogger(__name__)`).
- **No `print()` in library code.** Use logging. `run.py` configures it.
- **American spellings in code** (the libraries use them; consistency wins).

## Current state (September 2026)

Working:
- Storage layer (jobs, scores, feedback). Tested.
- Two-stage matcher with structured JSON response from Claude. Tested with synthetic data.
- FastAPI dashboard with thumbs feedback wiring. Renders correctly.
- Greenhouse and Lever scrapers — public APIs, well-documented field shapes. Untested live but parser logic is straightforward.
- **Getro JSON scraper** for Index Ventures (1629), Northzone (3791), Atomico (36986), Seedcamp (4186). All confirmed live.
- **Consider scraper** for Phoenix Court / LocalGlobe (`localglobe-all`), Notion Capital (`notion-capital`), Balderton (`balderton-capital`). All confirmed live. API: `POST /api-boards/search-jobs` with `board: {id, isParent}`, `query: {jobFunctions: ["Design"]}`, paginated via `meta.sequence` cursor. Since summer 2026 it also needs a CSRF token (otherwise `412 INVALID_CSRF`): the scraper loads the board page first for its session cookie and embedded `csrfToken`. That change silently emptied all three boards from roughly July until 22 October 2026.
- **Ashby scraper** — public JSON API, same shape as Greenhouse/Lever. Confirmed live and now the largest source by company count (60+ companies in `config.py`), so it dominates total fetched-job volume even though each company individually posts design roles rarely.
- **What the Roles list shows** — only roles posted (or, without a posted date, discovered) in the last `LIST_MAX_AGE_DAYS` (30, in `api/main.py`), with no count cap: older postings are usually filled or were never real. This is separate from `MAX_JOB_AGE_DAYS` (15) in config.py, which only decides what gets *scored*. Skips scoring `SKIP_MIN_SCORE` (20) or more are rendered but hidden behind the Skip chip; voting Interested on one promotes it into the main list (and the vote reaches the matcher as feedback, like any other). Below 20 they're clearly off-profile and aren't rendered at all.
- **Closed postings** — every fetch stamps each job it finds with `jobs.last_seen_at`, and records in `source_fetches` when each source (scraper: source + label) last returned jobs. A job is closed once its source has kept returning other jobs for over `CLOSED_AFTER_HOURS` (24, in `api/main.py`) without it, and drops off the list. A failed fetch, or one that returns nothing (indistinguishable from a scraper that quietly broke), is not recorded, so it never closes anything; the 30-day window is the fallback. Sources are recorded only after `process()` has saved every job, so a crash mid-run can't make jobs look closed. VC boards (Getro, Consider) can lag the company's own careers page in removing a posting; we only know what the boards show. Introduced 22 October 2026: the migration backfilled `last_seen_at = discovered_at`, so jobs no longer listed at the first fetch after that closed within a day.
- **Sources page** (`/sources`, local only — not in the export) — what every source did on the latest fetch: status (OK / Empty / Error with a short reason such as "404 Not Found · api.lever.co/…"), jobs returned, when it last returned jobs, and its newest posting. Problems sort first. Each run's outcomes are stored in `source_runs` (separate from `source_fetches`, which only records successes and drives closed-job detection). Empty is ambiguous by nature — a company not hiring and a broken scraper look the same, and the Getro/Consider scrapers swallow their own request errors and report Empty — so it only matters when it lasts. The first audit (22 October 2026) found six 404 boards (Ashby: DataSnipper, Aegiron Resilience, Lime, Kittl, Ona; Lever: Fresha) — removed from `config.py`, with a note there on where each went and all three Consider boards empty since June/July — Consider had added CSRF protection; fixed the same day. A Consider failure on the first page now raises, so it shows as Error rather than Empty.
- **Switching sources off** (Sources page, added 25 October 2026) — each row has a Fetch switch (a toggle switch, since it's a setting: blue when on), so a source can be dropped without editing `config.py`. The switch moves at once and goes back if the save fails, with a notification saying why (e.g. a server started before the code changed answers 404). Switches are stored in `sources_disabled.json` as `(source, label)` pairs, the identity `source_runs` and every job already carry. A switched-off source isn't fetched (`runner.fetch_all` skips it), and its roles are hidden on Roles at once, on the live export too, except under Applied. It stays on the Sources page, muted with an Off pill and its last outcome kept; switched back on, it shows "Next fetch" until the next run. Since sources skipped by a run now stay on record, `record_source_runs` instead prunes sources that are neither in the run nor switched off, i.e. removed from `config.py`. In this copy CI does the fetching, so a switch only reaches CI and the live site once `sources_disabled.json` is committed and pushed.
- **Applied chip** (local only — the live site doesn't read `feedback.json`) — every job marked Applied, whatever its age, verdict or status. Applications that have closed or passed the 30-day window leave the other chips and live only here, with a muted "Closed" pill if the posting has gone.
- **"New" pill on dashboard** — a job is New for `NEW_PILL_DAYS` (3) after it is scored (`scores.scored_at`), or after a re-score that changed its score. Small olive-green pill at the top-right of the card. It used to mean "scored during the latest fetch" (`last_fetch_at` in `metadata`, still recorded by the runner), but with two fetches a day and design roles arriving rarely, the pill was gone by the next run — usually before anyone looked. The static export bakes the pills in at export time, which runs on every fetch, so they expire there too.
- **Scheduling** — `.github/workflows/fetch.yml` runs `python run.py -v fetch` twice daily (07:00 and 15:00 UTC) via cron, plus a manual `workflow_dispatch` with a `force_rescore` input. `jobs.db` is committed back to the repo on every run (via Git LFS — see below), so CI and a local `git pull` always see the same database. The dashboard commit step only fires when `python run.py export` actually changes `public/` — across ~75 sources, most 12-hour windows turn up zero new *design* roles (the source companies are mature and design hiring is inherently low-frequency), so several-day gaps in dashboard content are expected, not a sign the schedule stopped running.
- **Live dashboard** is served by Cloudflare Pages from `public/`, deployed by Cloudflare's git integration on every push to `main`. CI's commit messages must **not** contain `[skip ci]`: Cloudflare honours that marker and skips the deploy, which is how scheduled fetches once updated the repo but never the live site (fixed 17 October 2026). The marker isn't needed on the GitHub side either — the workflow has no push trigger, and pushes made with `GITHUB_TOKEN` never trigger workflows.
- **`jobs.db` is Git LFS-tracked** (`.gitattributes`), not plain-git or `actions/cache`. It was plain-git until 5 July 2026, switched to `actions/cache`-only to stop local merge conflicts, then switched back on 21 September 2026 because cache-only meant no `git pull` ever refreshed a local dev's copy — the local interactive dashboard (voting/notes, via `python run.py serve`) silently went stale for 2.5 months as a result. LFS avoids the original merge-conflict problem (conflicts only happen if your local `jobs.db` diverges from what's committed — never run `fetch` locally against a copy you haven't just pulled) and avoids unbounded repo bloat from a twice-daily ~90MB+ binary commit. **Do not remove LFS tracking or go back to `actions/cache` for this file** without re-reading this note; both were tried and both had real failure modes.

- **Leads** (`python run.py detect`, local only — CI never runs it). Stored in `leads.db` (gitignored), separate from CI-owned `jobs.db`. Each run diffs every portfolio page against its previous snapshot, so only genuinely new companies are processed — nothing is re-looked-up on later runs. For each new company: the VC's detail page gives the company website, `careers.py` finds a careers page from it, and `enrich.py` asks Claude for HQ city and sector. Claude is told to answer "unknown" rather than guess, and never to use generic sector labels like "B2B SaaS". A scraper's `default_location` (e.g. "Europe") is a placeholder and is never stored as a location. Acquired companies are skipped. `python run.py detect --backfill` fills in missing location/sector/website/careers for existing leads.
- Working portfolio scrapers (October 2026): Index Ventures (HTML grid; tickers/acquirers sit in a `ticker-symbol` span inside the link), Northzone (Webflow CMS cards, paginated), Seedcamp (`/our-companies/`, one static page), Antler (Webflow CMS cards, paginated) and EF (WordPress; extra pages via the page's own admin-ajax `loadmore` action, website via `getcompany`). Antler and EF are global, so they keep only UK/European companies (`heuristic.is_european`). Seed-focused programmes publish the year they backed a company rather than its stage; `base.stage_from_year` treats this year or last as "seed".
- **Leads map** — a List / Map view switch in the Leads toolbar (text tabs, like Sort: it's a view, not a filter). The map sits above the usual list and detail and draws one bubble per place, sized by lead count; locations are only known to city level, so per-company pins would just stack. Places known only to country level ("Sweden") are hollow and dashed, labelled "city unknown", rather than faked as a city. Chips and search decide which leads count; clicking a bubble filters the list below to that place (a "London ✕" chip clears it). Coordinates come from OpenStreetMap's Nominatim — a lookup, not a judgement, so not Claude — once per distinct location, cached in `lead_places.json`; `detect` (and `detect --backfill`) places any new ones, one request a second as Nominatim asks. Location strings that geocode to the same point ("London", "London, United Kingdom") merge into one bubble, labelled with our own wording. Tiles are Esri's Light Gray Canvas (free without a key; CARTO's Positron needs one now). A lead's detail lists **Open roles**: the jobs the Roles list would show for that company, matched by name (so it inherits Getro's name-conflation wrinkle), each linking to `/#job=<id>`, which opens Roles on that job and switches chip if it's behind Skip or Applied.
- Lead sectors come from Claude for every source, so the whole list shares one vocabulary that matches `profile.json` (each VC words sectors differently — EF's "Financial Services" would never match "fintech"). The page's own sectors are passed in as a hint and kept only as a fallback.
- **Matcher model** is `claude-haiku-5-5` (moved from Haiku 4.5 on 15 October 2026). It thinks before answering, and thinking tokens count against `max_tokens` — keep that cap generous or the JSON verdict gets truncated.

- **Profile panel and country filter** (local `serve` only — the static export passes no `profile_path`, so the live site has neither). The panel edits every profile field and saves `profile.json` through `PUT /api/profile`, which validates against `models.Profile`; unknown keys are preserved. `location.countries` (ISO codes) is the country filter: `geo.LocationResolver` translates each distinct job location to country codes once and caches them in `locations.json`. A profile without `countries` — like the live `PROFILE_JSON` secret — keeps the old `would_not_relocate` pre-filter, the original scoring pool and no extra prompt line, so CI behaves exactly as before. With `countries`, the runner re-screens all unscored fetched jobs (not just new ones), so adding a country scores jobs fetched earlier, and the dashboard hides scored jobs outside the list. Unknown or unresolvable locations always pass.

- **jobs.db is kept small** (~10MB, down from 108MB on 17 October 2026) because every committed version counts against the free 10 GiB Git LFS storage quota for good. `Storage.compact()` runs at the end of every fetch: the `raw` column is no longer written (nothing read it, and it was ~75MB), descriptions are kept only for scored jobs (scoring always uses the freshly fetched copy), then `VACUUM`. The ~5GB of older versions committed before that still count until the repo is recreated or GitHub Support purges them; rewriting history alone doesn't free LFS storage.
- **Fetch button** (`LOCAL_FETCH=1` in `.env`, off by default). For copies that fetch on the laptop, not via CI. Starts `run.py fetch` as a subprocess (`api/fetch_runner.py`), reads its log lines for progress, and appends them to `fetch.log`; the page polls `GET /api/fetch`. Progress parsing matches the log messages in `run.py` and `runner.py` ("Fetching from", "will score:", "Scored [", "Done.") — change those messages and the progress display needs updating too. **Never enable it in a copy where CI owns `jobs.db`**: a local fetch would modify it.

Stubbed / pending:
- Workable scraper (has a public JSON endpoint similar to Greenhouse/Lever).

- **Saves never fail silently** (25 October 2026). Every save on Roles, Leads and Sources goes through `postJSON` in `_notify.html`, which treats any error answer as a failure (`fetch()` only throws when the server can't be reached, so a 404 used to pass for a save) and explains it in a notification at the bottom of the page. What changed on screen goes back as it was: a vote, Applied, a source switch; a hidden role returns on reload. A 404 without our own error message means the server predates the endpoint, and the notification says to restart it. The profile panel reports in its own status line instead. Notes, on Roles and Leads alike, save with a Save button or Enter, not on blur: a save you didn't ask for is one you can't tell has failed.
- **`serve` reloads itself** when any `.py` file changes (uvicorn's reloader, via the `run.local_app` factory), so the page and the server can't drift apart after a code change or `git pull`. Templates are read fresh on every request, and `/static/` is served with `Cache-Control: no-cache` so browsers check for a newer stylesheet instead of reusing a cached one for hours (which once made a CSS fix look broken). `.env` changes still need a manual restart.

Known wrinkles:
- `Storage` runs the same schema and migrations on every database it opens, including `jobs.db`. A new migration therefore modifies `jobs.db` on the first local run, before CI has committed a migrated copy — restore it with `git checkout -- jobs.db` rather than committing it.
- Getro has occasional company-name mismatches where two unrelated companies sharing a name get conflated (e.g. database company "Clickhouse" linked to "Clickhouse - Arquitetura Modular" — see NOTES.md). The job URL points to the real ATS posting so applications go to the right place, but the displayed company name can be misleading.
- The Getro listing endpoint doesn't include free-text descriptions. We synthesise a structured description from the typed fields (seniority, work mode, salary, industry tags). This is generally a denser signal than typical job-description marketing copy, but it does mean the matcher loses nuance from any unique role-specific language. Acceptable trade-off.

## How to test changes

For library code, the pattern that works well is a single `python -c` script that exercises imports plus the bit you changed. Examples:

```bash
# Verify all imports resolve
python -c "
import sys; sys.path.insert(0, '.')
import config
from job_radar.sources import GetroJsonScraper, GreenhouseScraper, LeverScraper
from job_radar.api.main import create_app
print('OK')
"

# End-to-end with synthetic data (storage + matcher + FastAPI test client)
# See the test scripts in the conversation history for templates.
```

There is no formal test suite yet. If adding one, prefer pytest with a single `tests/` directory and synthetic fixtures over network-dependent tests.

## Working with the matcher

The matcher prompt lives in `job_radar/matcher.py` as `SYSTEM_PROMPT` and `USER_PROMPT_TEMPLATE`. Tuning the prompt is the single most impactful lever on output quality, more so than fiddling with the rule-based pre-filter.

When the user reports "the matcher keeps recommending X" or "missing Y", the diagnosis is almost always one of:
1. The profile is under-specified — add a green/red flag, refine `must_have_keywords`
2. The prompt's rubric is too lenient — adjust the score band definitions
3. Claude isn't seeing the relevant signal — check that the source scraper is populating `description` (or the synthesised description, in Getro's case) with the field that matters

Reach for option 1 first. Profile changes are cheap, prompt changes are subtle, and missing signal is often a data problem, not a prompt problem.

## Style notes for the dashboard

The dashboard intentionally avoids generic AI-product aesthetics. Editorial reference: think of a personal newsroom or reading list, not a SaaS dashboard. When making visual changes, preserve this register; drift toward generic aesthetics is the thing to avoid. Tokens and every component shared by more than one page (list cards, detail header, pills, buttons, text tabs, underlined inputs, field labels) live in `job_radar/api/static/base.css`; `dashboard.css`, `leads.css` and `settings.css` hold only what is specific to their page. When Roles and Leads show the same kind of thing under different class names (`.card-title` / `.card-name`, `.source` / `.vc-label`), they share one grouped rule in base.css — add new shared styling there, not to a page file.

Type:
- **Plus Jakarta Sans** — the main face: body, masthead, buttons, job and lead titles, the profile panel.
- **IBM Plex Mono** — metadata and the nav row: page tabs, sort buttons, card meta lines, score labels, flags, status pills. The active page tab is weight 500; the active sort button stays regular. A wider-spaced `|` (`.nav-divider`) separates the page tabs from page-level info such as Leads' last scan.

The masthead nav row holds the page tabs, the search box (centred) and Profile. Search sits there, above the filters, because people arrive either looking for a specific role or company or browsing — and searching comes first. The live export drops the page tabs (it has no Leads page) but keeps the search. Search covers the current page only; when the active chip hides some of its results, a line above the list ("Also matching your search: 2 under Skip") links to them, so a search never comes back falsely empty. The browsing controls live in a full-width toolbar above the list/detail split (`.list-toolbar`, shared): filter chips on the left, then "Sort by:" (Roles) or "+ Add lead" (Leads) on the right. On Roles the default chip is "Matches" (every verdict but skip), not "All"; "Applied" follows Skip, local only. Remote is an independent toggle after a thin divider, not a sixth verdict chip; the divider is hidden on phones, where the chips wrap. On a phone, controls that wrap start at the left edge rather than hanging off the right. Filters are chips — the profile panel's `.chip-toggle`, set smaller — while sort stays as mono text tabs, so the two read as different kinds of control. Chip styles live in base.css because the live site doesn't load settings.css.

List cards (Roles and Leads) put the title line first and the meta line last, so on Roles the title sits level with the score — the thing you match it against on a phone. `.card-content` carries the right padding that keeps the title clear of the status pill.
- **Fraunces** — kept as a secondary voice for company names (`.card-company`, `.detail-company`) and a few lead-page details.

Colour (near-white paper, deep ink text, a single blue accent):
- `--pick` (blue `#0056e0`) — the accent: strong match scores (≥85), source/VC labels, "View posting" / "View site" buttons, selected-card border, selected country chips, focus rings, promoted-lead pill
- `--pick-soft` (`#3d73fb`) — the masthead divider
- `--good` (green `#157023`) — green flags, active up-vote, "New" pill, promoted leads, success text
- `--bad` (red `#b42318`) — red flags, active down-vote, down-vote count, "What doesn't" placeholder, error text
- `--weak` (muted `#b0a89a`) — weak/skip scores, stale dates
- `--select` (pale blue `#d8ecff`) — the selected list card; `--hover` — list card hover
- `--ink` fill — on/off states: active "Applied" button, the panel's Save button. Country chips were ink too, but moved to `--pick`: choosing chips is selection, which is what the blue already means (the selected-card border)
- `--ink-faint` / `--rule` — separators, secondary chrome

Keep `--pick` and `--bad` apart: the accent is blue, so using it for negative states made a red flag read as positive.
