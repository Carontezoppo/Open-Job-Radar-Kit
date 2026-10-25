# Job Radar

A personal job radar. It watches the job boards of VC portfolios and selected companies, scores every new role against your profile with Claude, and shows the best matches in a dashboard on your own laptop. It also spots companies newly added to VC portfolios, often before they post any jobs, and puts them on a map.

It started as one designer's job search and a way to learn Python, so every module does one thing and is meant to be readable.

## What it does

- **Roles**: jobs from the last 30 days, scored 0–100 against your profile with a short reasoning and green and red flags. Filter by verdict (Strong, Possible, Weak), New, Remote or Applied; search by title or company. Postings that are taken down drop off automatically. Near-misses sit under Skip, and voting one Interested moves it into the list.
- **Feedback**: vote ↑ or ↓ with a short note, and recent votes are shown to Claude on every later score, so it calibrates to you.
- **Leads**: companies newly added to VC portfolios, with HQ city, sector, website and careers page, on a list or a map, plus any open roles they have.
- **Sources**: a health check of every job board after each fetch, so a board that moves or breaks doesn't go quietly missing. Switch off any board you don't want, and its roles go with it.
- **Profile**: everything about what you want, edited in the dashboard: summary, roles, countries, sectors, flags and keywords.

## Who it's tuned for

Out of the box, the sources are aimed at **senior design roles (product, UX and service design, up to design leadership) at European startups**: the job boards of Index Ventures, Northzone, Atomico, Seedcamp, Phoenix Court, Notion Capital and Balderton, plus around 60 companies' own boards (Greenhouse, Lever, Ashby). The profile decides how jobs are scored, but the sources decide what can be found at all. To drop a board, switch it off on the Sources page. For a different field or region, edit the `SOURCES` list in `config.py`.

## What you need

- A Mac or Linux machine. The optional daily schedule in the setup guide uses macOS's built-in scheduler.
- Python 3.10 or later (3.12 recommended).
- Your own [Anthropic API key](https://console.anthropic.com/). Scoring uses Claude Haiku: the first run scores up to 200 jobs, and later runs only score new ones. Setting a monthly spend limit in the Claude Console is a good idea.
- [Claude Code](https://claude.com/claude-code), recommended for setup.

## Setup

The easy way: clone the repository, open it in Claude Code and ask it to set Job Radar up for you.

```bash
git clone <this repository's URL> job-radar
cd job-radar
claude
# then say: "set up Job Radar for me"
```

Claude follows a step-by-step guide (`.claude/skills/setup/SKILL.md`): it installs everything, has you add your API key (you paste it into `.env` yourself; it never asks for it in chat), drafts your profile with you, runs the first fetch and can schedule a daily one.

By hand:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# Edit .env: paste your key after ANTHROPIC_API_KEY=, and add the line LOCAL_FETCH=1
# for a "Fetch jobs" button on the dashboard

.venv/bin/python run.py serve
# Open http://localhost:8000, click Profile (top right), fill it in and Save,
# then click "Fetch jobs". The first fetch takes a few minutes.
```

Leads are found by a separate command, worth running every week or two:

```bash
.venv/bin/python run.py detect
```

## Updating

```bash
git pull
```

The dashboard restarts by itself when the code changes. Your profile, votes and databases aren't in git, so updates never touch them.

## How it works

1. **Sources** (`job_radar/sources/`) each turn one kind of job board into a list of `Job` objects: Greenhouse, Lever and Ashby (companies' own boards), Getro and Consider (VC portfolio boards). Adding a source means writing one class.
2. **Storage** is plain SQLite through `sqlite3`, no ORM, so the SQL is readable in `storage.py`.
3. **Matcher** (`matcher.py`) does two passes: a cheap rule-based pre-filter on your keywords and countries, then a Claude call for each survivor, returning a score, a verdict, reasoning and flags.
4. **Runner** (`runner.py`) fetches every source concurrently, de-duplicates against what's stored, filters, scores and saves, and records how each source did.
5. **Leads** (`job_radar/leads/`) diff each VC's portfolio page against the last snapshot, then look up each new company's HQ and sector, and place it on the map with OpenStreetMap.
6. **Dashboard** (`job_radar/api/`) is FastAPI serving Jinja templates; no front-end framework.

## Please fetch gently

The job boards are read through the same public endpoints their websites use. Once or twice a day is plenty, since new roles appear slowly, and keeps the load on them negligible. Please don't schedule it more often.

## Working on it with an AI assistant

- **`CLAUDE.md`** describes the architecture, conventions and current state, and is read at the start of every session.
- **`CLAUDE.local.md`** (created during setup, not in git) tells the assistant who you are and how you like things explained.
- **`NOTES.md`** is the project's log of decisions and discoveries, including how the Getro and Consider APIs were worked out.

## Contributing

Take it, change it, make it yours, and if you build something others could use, please send it back. Ideas, bug reports and pull requests are all welcome.

Good places to start:

- **New sources.** A Workable scraper is the obvious gap, and every source is one class in `job_radar/sources/`. Boards for other fields or regions make the radar useful to more people.
- **New VC portfolios for Leads**, one scraper each in `job_radar/leads/portfolio/`.
- **Anything that annoyed you** while setting it up or using it.

A few things keep it coherent:

- **Keep it readable.** Plain Python and the standard library where reasonable, one job per module, a docstring saying why. `CLAUDE.md` has the conventions, and your AI assistant will read it too.
- **For bigger changes, open an issue first**, so we can agree on the shape before you spend an evening on it.
- **Say how you tested it** in the pull request. There's no test suite yet, so a sentence on what you ran is enough.
- **No personal data in commits**: your profile, votes and databases are gitignored, so keep them that way.

## Licence

MIT. See [LICENSE](LICENSE).
