# Physical AI Job Search Agent

Automated job discovery, scoring, and tracking system for Physical AI / robotics / defense roles.

## Architecture

5 agents run on cron, writing results to a single Google Sheet ("Physical AI Job Search"):

| Agent | Trigger | What it does |
|---|---|---|
| Scout | 1st of month, 5am | Discovers new companies from VC portfolios via Claude + web_search |
| Watcher | Mon + Thu, 6am | Polls ATS endpoints for new job postings |
| Analyst | Mon + Thu, 6:30am | Scores new jobs against resume + profile using Claude |
| Postman | Daily, 7am | Reads Gmail for application emails, updates statuses |
| Briefer | Daily, 8am | Applies color coding to all Jobs rows |

---

## One-Time Setup

### 1. Google Cloud Console
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g., "job-agent")
3. Enable these APIs:
   - Google Sheets API
   - Google Drive API
   - Gmail API
4. Go to Credentials → Create Credentials → OAuth 2.0 Client ID
5. Application type: **Desktop app**
6. Download the JSON file → save as `~/job-agent/config/google_credentials.json`
7. Go to OAuth consent screen → add your Gmail address as a test user

### 2. Anthropic API Key
Get your key from [console.anthropic.com](https://console.anthropic.com)

### 3. Populate .env
```
nano ~/job-agent/config/.env
```
Fill in:
```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_CREDENTIALS_PATH=~/job-agent/config/google_credentials.json
GOOGLE_SHEET_ID=          # leave blank first run; auto-filled after first sheet access
GMAIL_USER=you@gmail.com
```

### 4. Drop in your resume
```
cp /path/to/your/resume.pdf ~/job-agent/config/resume_master.pdf
```

### 5. GitHub remote
Create a private repo called `job-agent` on GitHub, then:
```bash
git remote add origin https://github.com/YOUR_USERNAME/job-agent.git
git push -u origin main
```

### 6. First-run sequence
**Note: Scout requires `--force` on first run (bypasses the monthly-only guard).**
```bash
python3 ~/job-agent/scout/scout.py --force
python3 ~/job-agent/watcher/watcher.py
python3 ~/job-agent/analyst/analyst.py
```
The first time you run any agent, a browser window will open for Google OAuth. Approve access. The token is saved to `config/google_token.json` for future runs.

### 7. Set up cron
```bash
crontab -e
```
Paste:
```
# Physical AI Job Agent
0 5 1 * *    python3 ~/job-agent/scout/scout.py >> ~/job-agent/logs/scout.log 2>&1
0 6 * * 1,4  python3 ~/job-agent/watcher/watcher.py >> ~/job-agent/logs/watcher.log 2>&1
30 6 * * 1,4 python3 ~/job-agent/analyst/analyst.py >> ~/job-agent/logs/analyst.log 2>&1
0 7 * * *    python3 ~/job-agent/postman/postman.py >> ~/job-agent/logs/postman.log 2>&1
0 8 * * *    python3 ~/job-agent/briefer/briefer.py >> ~/job-agent/logs/briefer.log 2>&1
```

---

## Manual Run Commands

```bash
# Scout (force-run outside cron)
python3 ~/job-agent/scout/scout.py --force

# Watcher
python3 ~/job-agent/watcher/watcher.py

# Analyst
python3 ~/job-agent/analyst/analyst.py

# Postman
python3 ~/job-agent/postman/postman.py

# Briefer
python3 ~/job-agent/briefer/briefer.py
```

---

## How to Read the Sheet

**Tab: Jobs** — main job universe
- `status` flow: `new` → (Analyst) → `review` / `maybe` / `archived` → (you) → `applied` → (Postman) → `interviewing` / `rejected` / `offer`
- Color coding set by Briefer:
  - Green: fit_score 8-10
  - Yellow: fit_score 6-7
  - Gray: archived
  - Blue: interviewing
  - Orange (light): applied

**Tab: Scout Review** — pending company proposals from Scout
- Fill in `action` column: `add` to include, `skip` to ignore, leave blank to decide later
- Next Scout run will merge `add` rows into Company List and clear resolved rows

**Tab: Company List** — active company universe
- Tier 1: polled 2x/week by Watcher
- Tier 2: Scout checks monthly; auto-promotes to Tier 1 if careers page is live

**Tab: Email Events** — log of application-related emails from Gmail

---

## How to Add a Company Manually

Add a row directly to the **Company List** tab with:
- `company`, `careers_url`, `ats_type` (greenhouse/ashby/lever/workday/custom), `tier` (tier1/tier2), `active` (TRUE)

Watcher will pick it up on next run.

Alternatively, edit `config/company_list.yaml` and commit.

---

## How to Add a VC to vc_list.yaml

```bash
nano ~/job-agent/config/vc_list.yaml
```
Add a line: `- New VC Name  # brief note`

Scout will scan it on the next monthly run (or `--force`).

---

## How to Adjust Targeting (profile.yaml)

```bash
nano ~/job-agent/config/profile.yaml
```
- `target_functions`: job function categories to prioritize
- `target_function_keywords`: title keywords that auto-qualify a role (strategy, operations, GTM)
- `target_seniority`: preferred levels
- `exclude_title_keywords`: hard-exclude these title words (engineer, ML, etc.)

---

## Scout Review Tab Workflow

1. Scout runs monthly, writes proposed companies to Scout Review tab with `action` blank
2. You review the sheet: type `add` or `skip` in the `action` column
3. Next Scout run: `add` rows merge into Company List; `skip` rows are cleared; blank rows stay

---

## Git History

`config/company_list.yaml` is auto-committed after every successful Scout run:
```
Scout update: YYYY-MM-DD
```
This gives you a monthly diff of the company universe.

---

## Troubleshooting

**OAuth token expiry**
Delete `config/google_token.json` and re-run any agent. Browser will re-prompt for consent.

**ATS 404s**
Check `logs/watcher.log`. A company may have changed their ATS. Update the URL in `company_list.yaml` and the Company List sheet.

**Anthropic API rate limits**
Analyst processes many rows; if you hit limits, it will log errors and skip those rows. They'll be retried on next run (fit_score stays blank).

**gspread auth errors**
Usually token expiry. Delete `config/google_token.json` and re-authenticate.

**pypdf2 parse failures**
If `config/resume_master.pdf` is a scanned image PDF, text extraction will return empty. Convert to a text-based PDF using Adobe Acrobat or similar.

**Scout web_search not finding companies**
Check `logs/scout.log`. The Claude web_search tool requires the `claude-sonnet-4-6` model with `web_search_20250305` tool enabled and a valid `ANTHROPIC_API_KEY`.

**cron not running**
On macOS, cron requires Full Disk Access. Go to System Settings → Privacy & Security → Full Disk Access → add `/usr/sbin/cron`.
