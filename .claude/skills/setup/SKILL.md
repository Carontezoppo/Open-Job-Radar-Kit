---
name: setup
description: Set up a fresh copy of Job Radar for a new person on their own laptop — clear out any previous user's data, install, add their API key, build their profile, run the first fetch. Use when someone says they've been given a copy, asks how to install or get started, or asks to set Job Radar up for themselves.
---

# Setting up Job Radar for a new person

Each copy of Job Radar has one user, described for Claude in a gitignored `CLAUDE.local.md`. The person you're helping is setting up their own copy, either cloned from the shared GitHub repository or handed over as a folder. Don't carry over anyone else's profile, preferences or history. Ask their name at the start.

They'll run everything locally: `python run.py serve` for the dashboard and `python run.py fetch` to pull and score jobs, optionally every day via the Mac's scheduler (step 7). They don't need GitHub Actions, Cloudflare or the live site.

Work through the steps in order. Explain each one in a sentence before doing it, and check it worked before moving on. Most people setting this up aren't developers, so keep the terminal side light: run commands for them rather than asking them to type, and say what each one is for.

## Rules

- **Never ask for the API key in chat, and never print it.** They paste it into `.env` themselves, and you check it only by its prefix.
- **List what you'll delete and get a yes before deleting anything.** Wait for that confirmation before every deletion.
- **Don't install system software** (Homebrew, a new Python, Xcode tools) without asking first.

## 1. Check this is a fresh copy and clear out any previous user's data

Look at what's there with `ls -a` and `git remote -v` (the second may fail, which is fine).

**A clone of the shared repository** (`git remote -v` shows it) starts clean: keep `.git/`, because it's how they'll get updates later with `git pull`, and there's nothing to clear. Say so and move on to step 2.

**A folder handed over by someone else** may contain that person's data:

| Item | What it is | Action |
|---|---|---|
| `.git/` | The previous user's history, which can include their profiles, votes and databases | Offer to delete it and run `git init`, so their history starts clean |
| `CLAUDE.local.md` | The previous user's description, for Claude | Delete. Step 6 writes theirs |
| `profile.json` | The previous user's profile | Delete. The app falls back to `profile.example.json` |
| `.env` | May hold **the previous user's** API key | Delete. It's recreated in step 3 |
| `jobs.db`, `jobs_local.db` | Their fetched jobs and scores | Delete. A new one is created on the first fetch |
| `feedback.json` | Their votes and notes | Delete |
| `leads.db` | Their lead tracking | Delete |
| `lead_places.json` | Map coordinates for their leads' locations | Delete. Rebuilt by the first `detect` (step 8) |
| `locations.json` | Cache of location → country lookups | Safe to keep. It's just a cache and saves a few lookups. Delete only if they'd rather |
| `sources_extra.json` | Companies added as extra job sources | Ask them. Keeping it means more companies get searched |
| `sources_disabled.json` | Sources the previous user switched off | Delete, so every source starts on. They can switch off what they don't want on the Sources page |
| `public/` | A published dashboard | Delete |
| `.venv/` | A Python environment. It records absolute paths from the previous laptop, so it breaks when moved | Delete. Step 2 creates a fresh one |
| `.wrangler/` | Leftover local Cloudflare state | Delete |
| `.github/workflows/fetch.yml` | A scheduled cloud fetch | Delete if they'll push to their own GitHub. Otherwise it runs twice a day and fails without the previous user's secrets |
| Other notes files (not `NOTES.md`, which is the project's own log) | Someone's working notes | Mention them; they can keep or delete |

Show them this list for their copy, filtered to what's actually present, and delete only after a yes. If none of it is present, say so and move on.

## 2. Install

1. Run `python3 --version`. Use 3.12 if possible, which is what the project runs on in CI. If it's older than 3.10, or missing, stop and ask how they'd like to install a newer Python. On a Mac, the installer from python.org is the least intrusive option.
2. Create a virtual environment and install the dependencies:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
   Explain that the virtual environment keeps Job Radar's packages separate from everything else on the laptop. Use `.venv/bin/python` for every later command.
3. Check that the imports work:
   ```bash
   .venv/bin/python -c "import config; from job_radar.api.main import create_app; print('OK')"
   ```

## 3. Add their API key

1. `cp .env.example .env`
2. Ask them to open `.env` and replace `sk-ant-...` with their own Anthropic API key. They can create one in the Claude Console, under API keys. Suggest a separate key just for Job Radar, so its spending is easy to see.
3. Add `LOCAL_FETCH=1` to `.env` yourself (it's not secret). This shows a **Fetch jobs** button on the dashboard. Leave it out only in a copy where GitHub Actions fetches and commits `jobs.db`.
4. Check the key without revealing it:
   ```bash
   .venv/bin/python -c "import config; k = config.ANTHROPIC_API_KEY; print('key found' if k.startswith('sk-ant-') and len(k) > 20 else 'key missing or malformed')"
   ```

## 4. Build their profile

The profile decides what gets scored and how. It matters more than anything else in the setup.

1. Start the dashboard in the background with `.venv/bin/python run.py serve`, and ask them to open http://localhost:8000. If port 8000 is busy, add `--port 8001`.
2. Point them to **Profile** at the top right. Every field is there: summary, roles, countries, sectors, flags and keywords.
3. Offer to draft it with them. If they share a CV, a LinkedIn summary or a description of what they want, you can write:
   - the summary
   - roles open to and not interested in
   - sectors
   - green and red flags
   - keywords

   You can fill these in by editing `profile.json` directly, which they then review in the panel. Or you can give them the text to paste. If you write the file yourself, validate it first:
   ```bash
   .venv/bin/python -c "import json; from job_radar.models import Profile; Profile.model_validate(json.load(open('profile.json'))); print('valid')"
   ```
4. Draft with these points in mind:
   - **Keyword lists are literal.** `must_have_keywords` match as substrings ("design" catches "designer"), and a job needs at least one. `must_not_have_keywords` match whole words, and any one of them drops the job. Over-restrictive keywords are the commonest reason for an empty dashboard.
   - **Countries are ISO codes**, chosen in the panel. If none are selected, every location is considered.
   - **Everything is read by Claude** when it scores a job, so the summary and the flags shape the scores. Specific beats generic: "service design at scale" is more useful than "impact".
   - The job sources (in `config.py`) are mostly European VC portfolio boards. They limit what can be found more than the profile does.

## 5. First fetch

1. Explain what it does. It pulls jobs from about 70 sources, translates each location to countries once, drops jobs that fail the keyword and country filters, and scores the rest with Claude. Scoring is capped at 200 jobs per run, so the first run costs the most, and later runs only score new jobs. It takes a few minutes.
2. Ask them to click **Fetch jobs** on the dashboard (top right). The status beside it shows progress ("Scoring 12 of 40"), and a link appears when it's done. If you'd rather watch the full log, run `.venv/bin/python run.py fetch` yourself instead.
3. When it finishes, the last line of `fetch.log` shows the counts: `fetched`, `new`, `scored` and `skipped_prefilter`. Read them with the person:
   - `scored` is 0 and `skipped_prefilter` is high: the keywords or countries are too narrow. Look at the profile together.
   - "No ANTHROPIC_API_KEY" in the log: go back to step 3.
4. Ask them to refresh the dashboard.
5. Open the **Sources** tab together. It shows what each source did on that fetch: OK, Empty (the board had no jobs, which is often fine) or Error (with the reason, such as a 404). An Error that persists for several days means a company has moved its job board; offer to look into it. Any source they don't want, broken or simply not relevant, they can drop themselves with the **Fetch** switch on its row: switched off, it stops being fetched and its roles leave Roles; switched back on, it returns. No need to edit `config.py`.

## 6. Make the project theirs

1. Write `CLAUDE.local.md` with them: their name, background, how technical they are, and how they'd like explanations pitched. Claude reads it alongside CLAUDE.md in every later session, so future help is pitched for them. It's gitignored, so it stays on their laptop. Leave CLAUDE.md alone; it describes the code, and editing it would get in the way of updates.
2. Their personal files (`profile.json`, `feedback.json`, the databases, `.env`) are gitignored in the shared repository, so they never get pushed by accident. If they want a backup of those, a private repository or ordinary file backup is the place, never a public fork.

## 7. Daily automatic fetch (optional)

Offer this once the first fetch has worked. macOS's built-in scheduler (launchd) runs the fetch every morning. If the Mac is asleep at that time, it runs as soon as it wakes. If the Mac is off all day, that day is skipped. It doesn't use GitHub or any cloud service.

Don't set this up through GitHub Actions. The database would be committed to git twice a day, which uses up GitHub's free Git LFS storage within weeks, and it needs a pull/push routine to stay in sync.

1. **Check where the project lives.** macOS blocks background jobs from reading `~/Documents`, `~/Desktop` and `~/Downloads` unless Python is given Full Disk Access. If the project is in one of those folders, suggest moving it first, for example to `~/job_radar`. If they'd rather not, the fallback is adding `.venv/bin/python` under System Settings → Privacy & Security → Full Disk Access. If the project moves, re-create the virtual environment (step 2.2), because a venv records its own path.
2. **Ask what time they want.** Default to 08:00.
3. **Write `~/Library/LaunchAgents/com.jobradar.fetch.plist`.** Replace `PROJECT` with the project's absolute path (`pwd`), and set the hour and minute:
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
     <key>Label</key><string>com.jobradar.fetch</string>
     <key>ProgramArguments</key>
     <array>
       <string>PROJECT/.venv/bin/python</string>
       <string>PROJECT/run.py</string>
       <string>fetch</string>
     </array>
     <key>WorkingDirectory</key><string>PROJECT</string>
     <key>StartCalendarInterval</key>
     <dict>
       <key>Hour</key><integer>8</integer>
       <key>Minute</key><integer>0</integer>
     </dict>
     <key>StandardOutPath</key><string>PROJECT/fetch.log</string>
     <key>StandardErrorPath</key><string>PROJECT/fetch.log</string>
   </dict>
   </plist>
   ```
   The API key is read from the project's `.env`, so the plist needs no environment settings. For a second daily run, `StartCalendarInterval` can be an `<array>` of two such `<dict>`s.
4. **Turn it on and test it straight away**, without waiting for the scheduled time:
   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jobradar.fetch.plist
   launchctl kickstart gui/$(id -u)/com.jobradar.fetch
   ```
   After a minute, run `tail -20 fetch.log`. It should show the same log as a manual fetch and end with the `Done.` counts. "Operation not permitted" means the folder problem from step 7.1.
5. **Tell them how to manage it:**
   - **Check the last run:** open `fetch.log` in the project folder. It grows by a few hundred lines a day; delete it whenever, and a new one is created.
   - **Change the time:** edit the plist, then run `launchctl bootout gui/$(id -u)/com.jobradar.fetch` followed by the `bootstrap` command again.
   - **Turn it off:** run `launchctl bootout gui/$(id -u)/com.jobradar.fetch` and delete the plist.

## 8. Day-to-day use

Finish by telling them how to use it from now on:

- Start the dashboard: `.venv/bin/python run.py serve`, then open http://localhost:8000.
- New jobs arrive with the daily fetch (if set up in step 7). To fetch now, for example right after a profile change, click **Fetch jobs**. Once or twice a day is plenty; design roles appear slowly.
- Voting ↑ or ↓ with a short note (Save, or Enter, keeps it) teaches the scorer. Recent votes are shown to Claude on every later score.
- After a big profile change, tick **Re-score all** before clicking **Fetch jobs**. It re-scores every job, so it costs about as much as a first run.
- **Roles** shows jobs posted in the last 30 days that the matcher didn't skip. Jobs whose posting has been taken down drop off a day after the source stops listing them. **Skip** holds the near-misses; voting ↑ Interested on one moves it into the main list.
- **Mark as applied** on every application. The **Applied** chip keeps all of them in one place, even after the posting closes or ages out of the list.
- **Leads** are companies newly added to VC portfolios, often before they post any jobs. Run `.venv/bin/python run.py detect` every week or two (it's not part of the daily fetch). The first run records each portfolio as it is now; later runs find the companies added since. It also places each company's city on the **Map** view (List / Map, in the Leads toolbar), and a lead's detail lists any open roles at that company.
- **Sources** is worth a glance now and then, as in step 5.5.
- **Updates:** in a clone of the shared repository, `git pull` brings in new features and fixes. Their profile, votes and data are untouched, since none of it is in git. A running dashboard restarts by itself when the code changes.

## Troubleshooting

- **`file is not a database` on `jobs.db`:** the copy came from GitHub without Git LFS, so `jobs.db` is a small pointer file. Delete it; a real one is created on the next fetch.
- **Dashboard says "Nothing scored yet":** no fetch has run yet, or it scored nothing. See step 5.3.
- **No Fetch jobs button:** `LOCAL_FETCH=1` is missing from `.env`, or the dashboard wasn't restarted after adding it.
- **"Fetch failed" beside the button:** the message is the last log line; `fetch.log` has the full run.
- **No Map switch on Leads:** `detect` hasn't run yet, so no lead locations have been placed. Run it (step 8). Leads with an unknown location never appear on the map; the line under it counts them.
- **Sources page says "No fetch recorded yet":** no fetch has run since the copy was set up. It fills in after the next one.
- **Profile changes don't seem to affect scores:** they apply to jobs scored from the next fetch on. Jobs already scored keep their score unless you force a rescore. Countries are the exception: they take effect as soon as the profile is saved.
