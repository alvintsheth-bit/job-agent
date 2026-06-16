# Physical AI Job Search Agent

An automated job search system targeting Physical AI, robotics, defense tech, and autonomous systems companies. Discovers companies, fetches job listings, scores them against a resume, tracks application emails, and maintains a Google Sheets dashboard.

## Architecture

Five agents run on a weekly schedule (every Monday):

| Agent | Schedule | What it does |
|-------|----------|-------------|
| **Watcher** | 6:00am | Polls ATS APIs (Greenhouse, Ashby, Lever, BambooHR, Workday, custom) for new job listings |
| **Analyst** | 6:30am | Scores each new job 1–10 against resume using Claude Haiku |
| **Postman** | 7:00am | Checks Gmail for recruiter/ATS response emails and classifies them |
| **Briefer** | 7:30am | Color-codes the Jobs tab in Google Sheets by fit score |
| **Scout** | Manual only | Discovers new Physical AI companies from VC portfolios via web search |

## Stack

- **Claude Haiku 4.5** — job scoring (Analyst), email classification (Postman), company discovery (Scout)
- **Google Sheets** — dashboard (Jobs, Scout Review, Email Events, Company List tabs)
- **Gmail API** — application tracking via OAuth2
- **ATS APIs** — Greenhouse, Ashby, Lever, BambooHR (direct JSON), Workday + custom (HTML scrape)
- **crontab** — weekly scheduling on macOS

## Cost

~$4.50/month ongoing:
- Scout (manual, ~quarterly): ~$2–5/run
- Analyst (weekly, ~100 new jobs): ~$1–2/month
- Postman + Briefer: <$0.50/month
- Watcher: free (direct ATS API calls, no LLM)

## Setup

```bash
# Install dependencies
pip3 install -r requirements.txt

# Configure
cp config/.env.example config/.env
# Fill in: ANTHROPIC_API_KEY, GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, GMAIL_USER

# Drop resume in
cp your_resume.pdf config/resume.pdf

# Authenticate Google (one-time)
python3 shared/sheets.py

# Run manually
python3 watcher/watcher.py
python3 analyst/analyst.py
python3 briefer/briefer.py
python3 postman/postman.py

# Discover new companies (manual, run quarterly)
python3 scout/scout.py --force --vc-limit 0
```

## Company Coverage

- **Tier 1** (~140 companies): Active careers pages, polled weekly by Watcher
- **Scout Review**: Proposed companies from VC portfolio scans — set `tier` to `tier1` to activate
- **VCs tracked**: 31 firms including Eclipse, Lux, Founders Fund, a16z, Khosla, GV, Sequoia, General Catalyst, and more

## Google Sheet

Jobs tab columns: `company`, `role_title`, `fit_score`, `function_category`, `level_match`, `fit_rationale`, `resume_hook`, `url`, `status`, `applied_date`

Status values: `new` → `review` (score 7+) / `maybe` (5–6) / `archived` (1–4) → `applied` → `interviewing` → `offer` / `rejected`

## Files

```
config/
  .env                  # API keys (never committed)
  profile.yaml          # Target functions, keywords, exclude list
  company_list.yaml     # Tier 1 + Tier 2 company ATS URLs
  vc_list.yaml          # 31 VCs to scan with Scout
  resume.pdf            # Your resume
scout/scout.py          # VC portfolio discovery
watcher/watcher.py      # ATS job fetcher
analyst/analyst.py      # Resume fit scorer
postman/postman.py      # Gmail application tracker
briefer/briefer.py      # Sheet color-coder
shared/sheets.py        # Google Sheets client (batch writes, retry)
shared/resume.py        # Resume parser
fix_ats_urls.py         # One-off: resolves ATS API URLs via slug guessing
```
