# Project notes

Running log of decisions, discoveries, and open questions. Append-only by convention; older entries stay even when superseded so we can see how thinking evolved.

---

## Project goal

Build a personal tool that surfaces relevant senior design roles before they hit the major aggregators. Specifically:

- **Freshness** — VC portfolio boards (Index, Northzone, Atomico, Balderton, Seedcamp, etc) tend to publish 1–2 weeks earlier than LinkedIn or Indeed. Catch them there.
- **Relevance** — use Claude to filter against a structured profile rather than just keyword matching, so context like "strategic remit" or "0→1 product" can actually count.
- **Low effort** — runs on cron, surfaces a small daily set, learns from thumbs feedback over time.
- **Educational** — the scaffolding is also a Python learning vehicle. Readability over cleverness throughout.

---

## Architectural decisions

### Why a single-file SQLite database

Considered: Postgres, Supabase, JSON files on disk.

Chose SQLite because:
- One user, one machine, no concurrency requirements
- The whole database file can be committed for backup or moved across machines
- The `sqlite3` stdlib module is genuinely teachable — every SQL statement in `storage.py` is visible
- Avoids the cognitive overhead of an ORM at this scale

If the project ever grows to multi-user or scheduled remote runs, swapping to Postgres is mostly a connection-string change since we use parameterised queries throughout.

### Why two-stage matching (rules then AI)

The naive approach is "send everything to Claude." The reasons not to:

1. **Cost** — Index Ventures alone has ~13,000 jobs. Even at Haiku prices, scoring all of them every run is wasteful.
2. **Quality** — Claude is good at nuance but doesn't need to weigh in on a "Junior Graphic Designer" role to know it's a no.
3. **Speed** — the rule-based pass happens in milliseconds and removes ~80% of obvious mismatches.

The pre-filter is intentionally crude (substring match against profile keywords). It only catches the obvious cases, leaving Claude to do the actual judgement work on the survivors.

### Why structured JSON output from Claude rather than free text

`matcher.py` asks Claude for a JSON object: `{score, verdict, reasoning, green_flags, red_flags}`. The reasons:

- Storable — fits naturally into SQLite columns
- Filterable — the dashboard can sort by score, group by verdict
- Faithful — the rubric is encoded in the prompt; Claude has to commit to a number, not hedge with "this could be a great fit"
- Inspectable — `red_flags` and `green_flags` make it visible *why* a score is what it is, which feeds the prompt-tuning loop

### Why Haiku rather than Sonnet/Opus for scoring

Cost per job is the deciding factor. The scoring task (structured JSON output following a clear rubric, with a profile and a job posting as input) is well within Haiku's capability. Sonnet would be marginally better at nuance but ~5x more expensive.

If we ever want a deep write-up on a top-3 match, that's where to call Sonnet or Opus. That's a future feature, not the default.

### Why FastAPI + Jinja templates rather than React + an API

Single-user local tool. No build step, no node_modules, no dev server orchestration. The whole frontend is one HTML file with ~30 lines of vanilla JS. Editing the dashboard means editing one file in VS Code and reloading the browser.

If interaction patterns ever get complex enough to want React, the FastAPI side is already structured to serve as a JSON API — `/api/feedback` is already there, and the dashboard route could return JSON instead.

---

## Discovery log

### Getro VC boards: API reverse-engineering

**Investigation date**: April 2026

**Context**: Started by trying to scrape `indexventures.getro.com/jobs`. Initially assumed an Algolia search backend (typical for SPAs at this scale). Wrong — it's Getro's own REST API.

**Finding 1**: The first page of jobs is server-rendered into HTML. No client-side fetch fires on initial page load. This is why the network tab on a fresh load showed only configuration/facet endpoints.

**Finding 2**: Subsequent pages of jobs — triggered by "Load more" or by typing a search query — fire requests to `https://api.getro.com/api/v1/collections/{collection_id}/jobs`.

**Finding 3**: The API rejects requests with no `Origin` header (likely CORS-style allowlist on the server side). Spoofing `Origin: https://{subdomain}.getro.com` makes it work from a Python client.

**Finding 4**: Each VC has its own numeric `collection_id` baked into Getro's data model. Index Ventures = 1629. To find others: open the VC's board, inspect any request to `api.getro.com` in DevTools — the number after `/collections/` is what we want.

**Confirmed endpoint shape** (`/jobs`):

```json
{
  "results": {
    "jobs": [
      {
        "id": 75812750,
        "slug": "75812750-product-designer",
        "title": "Product Designer",
        "url": "https://boards.greenhouse.io/clickhouse/jobs/5977068004",
        "work_mode": "remote",
        "seniority": "mid_senior",
        "locations": ["United States", "Remote"],
        "searchable_locations": ["United States", "North America", "Remote"],
        "created_at": 1776873512,
        "compensation_amount_min_cents": 14200000,
        "compensation_amount_max_cents": 19700000,
        "compensation_currency": "USD",
        "compensation_period": "year",
        "compensation_offers_equity": true,
        "skills": [],
        "has_description": true,
        "source": "career_page",
        "featured": false,
        "weight": 0,
        "organization": {
          "id": 71286,
          "name": "Clickhouse - Arquitetura Modular",
          "slug": "clickhouse-arquitetura-modular",
          "logo_url": "https://cdn.getro.com/companies/...",
          "stage": "other",
          "head_count": 1,
          "industry_tags": ["Architecture", "Design", "Construction"],
          "topics": []
        },
        "location_details": [...]
      }
    ]
  }
}
```

**Notable**: `description` is **not** in the listing payload. The `has_description: true` flag suggests it's available via a separate endpoint (likely `/jobs/{id}`), but fetching that per-job means thousands of extra requests per run. Decision: synthesise a structured description from the typed fields (`seniority`, `work_mode`, salary, `industry_tags`, etc) and feed that to the matcher. See `_synthesise_description` in `sources/getro.py`.

### Greenhouse and Lever

Both are well-documented public APIs, no investigation needed:

- **Greenhouse**: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` — returns full descriptions as HTML in the `content` field. Token is the slug at the end of the public board URL.
- **Lever**: `https://api.lever.co/v0/postings/{company}?mode=json` — returns `descriptionPlain` directly, plus structured `lists` for responsibilities/requirements.

---

## Open questions

### Where to find each VC's collection ID

Outstanding. To migrate the four remaining VCs from HTML to JSON, we need:

| VC          | Subdomain                          | Collection ID  |
|-------------|------------------------------------|----------------|
| Index Ventures | indexventures                   | 1629 ✓         |
| Northzone   | opportunities.northzone            | ?              |
| Atomico     | careers.atomico                    | ?              |
| Balderton   | careers.balderton                  | ?              |
| Seedcamp    | talent.seedcamp                    | ?              |

Once filled in, update `config.SOURCES` to use `getro_json` with each.

### Per-job description fetch — worth it?

Currently we synthesise descriptions from typed fields. Open question: is the loss of free-text content meaningful for matcher quality, or is the structured signal sufficient?

Way to test: pick 10 jobs the matcher scored as `weak_match` or `possible_match`, manually fetch the real description from the underlying ATS, re-score with the description included, and see if verdicts change. If they do — wire up a per-job description fetch as an opt-in second pass for ambiguous jobs only.

### Feedback loop into the prompt

The thumbs system writes to the `feedback` table but doesn't currently feed back into the matcher. Two ways it could:

1. **Manual** — periodically review feedback, hand-update the profile or the prompt rubric
2. **Automatic** — include the last N feedback decisions as few-shot examples in the matcher prompt

Option 2 is more interesting but risks the matcher overfitting to recent moods. Start with option 1 and only escalate if patterns emerge that the profile alone can't capture.

### Notification freshness

The current design has no notification mechanism — you have to look at the dashboard. Once GitHub Actions cron is set up, the gap between "new role posted" and "you find out" is at most the cron interval plus how often you remember to check the dashboard. Acceptable for now. If this becomes the bottleneck, consider:

- A daily digest email of new strong_matches only (one-line signal, dashboard for the detail)
- A simple RSS feed exposed by FastAPI that you read in your existing reader

---

## Known data quality issues

### Getro company-name conflation

Getro occasionally conflates unrelated companies that share a name. Observed example: the database company **Clickhouse** has a Greenhouse careers board at `boards.greenhouse.io/clickhouse`. Getro's organization record for this is named "Clickhouse - Arquitetura Modular" (a Brazilian architecture firm, unrelated). The job URL correctly points to the database company's Greenhouse board, but the displayed company name is wrong.

Mitigation: the matcher sees both the synthesised description (with industry tags) and the URL. For obvious conflations Claude usually figures it out from context. Worth flagging in the dashboard if it becomes systematic, but not worth fixing automatically — the source of truth is Getro's data.


## Interesting unexpected events

###Getro v2 endpoint mystery 
Of 107 jobs, none of those 20 Index Ventures jobs came from this run. The current run shows the v2 endpoint returning 404 four candidate paths in a row, then [getro/Index Ventures] fetched 0 jobs. Today's run contributed nothing new from Index. Possible causes: Getro requires a session cookie that the browser obtains by visiting, Cloudflare bot protection kicked in after several runs from the same IP. or A required header we're not sending. 

###We need to fine tune the prompt
Claude has correctly identified a number of suitable roles, however some are different specialisation. This happened because there wasn't enough data to make a correct assessment (e.g. content design is a very different field of expertise) We need to include a more specific set of parameters.

### Tuning the profile
Engineer roles slipping through on 'Domain Driven Design'" goes in. "Brand designer roles slipping through because 'design' is in title

## Updates on the 28th of April

###Shipped so far
Cache invalidation via --force-rescore, tiered relocation that actually moved European role scores by 10–24 points each, a freshness filter, three new VC sources (Northzone, Atomico, Seedcamp) with Balderton scoped for later, an extended pre-filter that catches engineering false-positives, the cron itself, action version bumps, and the profile rename. 

###Things to look at
Scraper for Balderton uses Consider (consider.com), company_size noise in the matcher prompt, apparently company size is misrappresented by Getro API, Getro's full-text search returning engineering jobs on query=design due to the vast usage of the term "Design" for multiple professions.

### On grading
the system should probably mark jobs as "applied" rather than "interested" with date metadata, because those are different signals. Interested = "this looks good." Applied = "I committed enough time to formally apply."

---

## Consider.com API reverse-engineering (April 2026)

**Context**: Balderton, Phoenix Court / LocalGlobe, and Notion Capital all use Consider-powered job boards.

**Finding 1**: Pages are server-rendered (React + SSR via a framework called Mendel). The initial HTML includes `window.serverInitialData` which embeds the board config, including `board.id`.

**Finding 2**: Jobs are loaded client-side via a POST to `/api-boards/search-jobs` on the same domain. No auth required. The Origin header must match the board's domain (same-origin style check).

**Finding 3**: The `board` parameter must be an object `{id: "<slug>", isParent: true}`, not a string. Sending the slug as a string returns a RethinkDB-level error (`Cannot wrap undefined with r.expr()`).

**Finding 4**: Server-side filtering by `jobFunctions: ["Design"]` works well and is semantically cleaner than `jobTypes`. It's the right filter for this use case.

**Finding 5**: Pagination uses a cursor in `meta.sequence` (base64-encoded RethinkDB cursor). Send `meta: {}` for the first page, then pass back the response's `meta` object for subsequent pages. Last page indicated by absent `sequence` or partial page.

**Finding 6**: No free-text job description in the listing payload. Structured fields (role type, seniority, salary, skills, markets, stages) are synthesised into a description for the matcher — same trade-off as Getro.

**Board IDs confirmed**:

| VC | Domain | board_id |
|---|---|---|
| Phoenix Court / LocalGlobe | jobs.phoenixcourt.vc | `localglobe-all` |
| Notion Capital | jobs.notion.vc | `notion-capital` |
| Balderton | careers.balderton.com | `balderton-capital` |

**Live counts (30 April 2026)**: Phoenix Court 74 design jobs, Notion Capital 5, Balderton 31.

---

## Updates on the 30th of April

### Shipped
- Consider scraper (`sources/consider.py`) — three new VC boards live: Phoenix Court / LocalGlobe, Notion Capital, Balderton. Filters server-side by `jobFunctions: ["Design"]`. Synthesises descriptions from typed fields (role type, seniority, salary, skills, industry, stage) — same pattern as Getro.
- "New" freshness pill on the dashboard — small olive-green badge, top-right of each list card. Backed by a `metadata` table in SQLite (`key/value`). `run.py` records `last_fetch_at` (fetch start time) at the end of each run; the dashboard marks jobs with `discovered_at >= last_fetch_at`. Pill resets automatically on the next fetch.

### Still open
- Company size from Getro API appears to be unreliable / misrepresented — may be worth suppressing from the synthesised description to reduce noise in the matcher prompt.
- The "applied" vs "interested" distinction in feedback (noted April 28) — still unaddressed.
