#!/usr/bin/env python3
"""Scout: discovers new Physical AI companies from VC portfolios via Claude web_search."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

import anthropic
import requests
import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.expanduser("~/job-agent"))
from shared.db import get_last_run, set_last_run
from shared.sheets import get_all_rows, append_row

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scout] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR = os.path.expanduser("~/job-agent")
VC_LIST_PATH = os.path.join(BASE_DIR, "config/vc_list.yaml")
COMPANY_LIST_PATH = os.path.join(BASE_DIR, "config/company_list.yaml")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-agent/1.0)"}


def load_vc_list() -> list[str]:
    with open(VC_LIST_PATH) as f:
        raw = yaml.safe_load(f)
    # Each item is a string like "Eclipse Ventures  # comment"
    vcs = []
    for item in raw:
        if isinstance(item, str):
            name = item.split("#")[0].strip()
            if name:
                vcs.append(name)
    return vcs


def load_company_list() -> dict:
    with open(COMPANY_LIST_PATH) as f:
        return yaml.safe_load(f)


def existing_company_names(company_data: dict) -> set[str]:
    names = set()
    for tier in ["tier1", "tier2"]:
        for c in company_data.get(tier, []):
            names.add(c.get("company", "").lower())
    return names


def git_commit_company_list():
    try:
        subprocess.run(
            ["git", "add", "config/company_list.yaml"],
            cwd=BASE_DIR, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"Scout update: {datetime.now().strftime('%Y-%m-%d')}"],
            cwd=BASE_DIR, check=True, capture_output=True
        )
        log.info("Git commit: company_list.yaml updated")
    except subprocess.CalledProcessError as e:
        log.warning(f"Git commit failed — continuing anyway: {e}")


def url_is_live(url: str) -> bool:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        return resp.status_code == 200
    except Exception:
        return False


def find_careers_url(client: anthropic.Anthropic, company_name: str) -> tuple[str, str]:
    """Returns (careers_url, ats_type). Uses Claude web_search to find it."""
    prompt = (
        f"Find the ATS API endpoint for '{company_name}' jobs. "
        f"If Greenhouse, return: https://boards-api.greenhouse.io/v1/boards/SLUG/jobs?content=true "
        f"If Ashby, return: https://api.ashbyhq.com/posting-api/job-board/SLUG "
        f"If Lever, return: https://api.lever.co/v0/postings/SLUG?mode=json "
        f"If Workday, return the Workday careers URL. "
        f"If none of the above, return the human careers page URL and set ats_type to custom. "
        f"Return JSON only: {{\"careers_url\": \"...\", \"ats_type\": \"greenhouse|ashby|lever|workday|custom\"}}"
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from response
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text
        import json, re
        match = re.search(r'\{[^}]+\}', text)
        if match:
            data = json.loads(match.group())
            return data.get("careers_url", ""), data.get("ats_type", "custom")
    except Exception as e:
        log.error(f"  careers URL search failed for {company_name}: {e}")
    return "", "custom"


def discover_companies_for_vc(client: anthropic.Anthropic, vc_name: str,
                               existing: set[str]) -> list[dict]:
    prompt = (
        f"List Physical AI, robotics, defense tech, autonomous vehicles, or industrial automation "
        f"portfolio companies backed by '{vc_name}'. "
        f"Only include companies operating in the physical world (hardware, robots, drones, "
        f"autonomous systems, defense). Exclude pure software SaaS companies. "
        f"Return JSON array only: "
        f'[{{"company": "...", "rationale": "brief reason why physical AI"}}, ...]'
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text

        import json, re
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if not match:
            return []
        companies = json.loads(match.group())
        new_companies = [
            c for c in companies
            if c.get("company", "").lower() not in existing
        ]
        return new_companies
    except Exception as e:
        log.error(f"  Discovery failed for {vc_name}: {e}")
        return []


def promote_tier2_companies(company_data: dict) -> bool:
    """Check Tier 2 companies; auto-promote to Tier 1 if careers URL is live."""
    changed = False
    tier1 = company_data.get("tier1", [])
    tier2 = company_data.get("tier2", [])
    tier1_names = {c["company"] for c in tier1}
    still_tier2 = []

    for company in tier2:
        if not company.get("active", True):
            still_tier2.append(company)
            continue
        url = company.get("url", "")
        if url and url_is_live(url):
            company["tier"] = "tier1"
            tier1.append(company)
            tier1_names.add(company["company"])
            log.info(f"  AUTO-PROMOTED to Tier 1: {company['company']}")
            # Also update Company List sheet
            try:
                from shared.sheets import find_row, update_row
                result = find_row("Company List", "company", company["company"])
                if result:
                    _, row_idx = result
                    update_row("Company List", row_idx, {"tier": "tier1"})
            except Exception as e:
                log.warning(f"  Sheet update failed for {company['company']}: {e}")
            changed = True
        else:
            still_tier2.append(company)

    company_data["tier1"] = tier1
    company_data["tier2"] = still_tier2
    return changed


def process_scout_review_approvals(company_data: dict) -> bool:
    """Merge action='add' rows from Scout Review tab into Company List."""
    changed = False
    try:
        rows = get_all_rows("Scout Review")
        existing = existing_company_names(company_data)
        tier1 = company_data.get("tier1", [])
        tier2 = company_data.get("tier2", [])

        approved = [r for r in rows if str(r.get("action", "")).strip().lower() == "add"]
        for row in approved:
            name = row.get("company", "")
            if not name or name.lower() in existing:
                continue
            tier = row.get("tier", "tier2")
            entry = {
                "company": name,
                "url": row.get("careers_url", ""),
                "ats": row.get("ats_type", "custom"),
                "slug": None,
                "vc": row.get("vc_backer", ""),
                "tier": tier,
                "active": True,
            }
            if tier == "tier1":
                tier1.append(entry)
            else:
                tier2.append(entry)
            existing.add(name.lower())
            # Add to Company List sheet
            from shared.sheets import append_row as sheet_append
            from datetime import date
            sheet_append("Company List", {
                "company": name,
                "careers_url": row.get("careers_url", ""),
                "ats_type": row.get("ats_type", "custom"),
                "vc_backer": row.get("vc_backer", ""),
                "tier": tier,
                "date_added": date.today().isoformat(),
                "active": "TRUE",
            })
            log.info(f"  MERGED from Scout Review: {name}")
            changed = True

        company_data["tier1"] = tier1
        company_data["tier2"] = tier2

        # Clear processed rows (action='add' or action='skip') from Scout Review
        from shared.sheets import get_or_create_spreadsheet
        ss = get_or_create_spreadsheet()
        ws = ss.worksheet("Scout Review")
        all_rows = ws.get_all_records()
        headers = ws.row_values(1)
        rows_to_clear = []
        for i, r in enumerate(all_rows):
            action = str(r.get("action", "")).strip().lower()
            if action in ("add", "skip"):
                rows_to_clear.append(i + 2)  # 1-indexed + header
        for row_idx in sorted(rows_to_clear, reverse=True):
            ws.delete_rows(row_idx)
        if rows_to_clear:
            log.info(f"  Cleared {len(rows_to_clear)} rows from Scout Review tab")

    except Exception as e:
        log.error(f"Scout Review processing failed: {e}")
    return changed


def save_company_list(company_data: dict) -> None:
    with open(COMPANY_LIST_PATH, "w") as f:
        yaml.dump(company_data, f, default_flow_style=False, allow_unicode=True)


def run(force: bool = False, vc_limit: int = 5):
    log.info(f"Scout starting (force={force})")

    # Check if already ran this month
    last = get_last_run("scout")
    now = datetime.now(timezone.utc)
    if not force and last and last.year == now.year and last.month == now.month:
        log.info(f"Already ran this month ({last.date()}). Use --force to override.")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    company_data = load_company_list()
    existing = existing_company_names(company_data)

    # Step 1: Auto-promote Tier 2 companies
    log.info("Checking Tier 2 companies for promotion...")
    tier2_changed = promote_tier2_companies(company_data)

    # Step 2: Process any approved Scout Review rows
    log.info("Processing Scout Review approvals...")
    review_changed = process_scout_review_approvals(company_data)

    # Step 3: Discover new companies via VC portfolios
    vcs = load_vc_list()
    log.info(f"Scanning {len(vcs)} VCs for new portfolio companies...")

    # Reload existing after merges
    existing = existing_company_names(company_data)
    new_proposals = []

    for vc in vcs[:vc_limit]:
        log.info(f"  Searching: {vc}")
        candidates = discover_companies_for_vc(client, vc, existing)
        for candidate in candidates:
            company_name = candidate.get("company", "").strip()
            if not company_name or company_name.lower() in existing:
                continue
            rationale = candidate.get("rationale", "")
            log.info(f"    Candidate: {company_name}")

            # Find careers URL
            careers_url, ats_type = find_careers_url(client, company_name)
            tier = "tier1" if (careers_url and url_is_live(careers_url)) else "tier2"

            proposal = {
                "company": company_name,
                "careers_url": careers_url,
                "ats_type": ats_type,
                "vc_backer": vc,
                "tier": tier,
                "rationale": rationale,
                "action": "",  # User fills in 'add' or 'skip'
            }
            new_proposals.append(proposal)
            existing.add(company_name.lower())

    # Write proposals to Scout Review tab
    log.info(f"Writing {len(new_proposals)} proposals to Scout Review tab...")
    for p in new_proposals:
        try:
            append_row("Scout Review", p)
        except Exception as e:
            log.error(f"  Failed to write proposal for {p['company']}: {e}")

    # Save updated company_list.yaml
    if tier2_changed or review_changed:
        save_company_list(company_data)
        git_commit_company_list()

    set_last_run("scout")
    log.info(
        f"Scout done: {len(new_proposals)} new proposals, "
        f"Tier 2 promotions: {tier2_changed}, Review merges: {review_changed}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scout: discover new Physical AI companies")
    parser.add_argument("--force", action="store_true", help="Run even if already ran this month")
    parser.add_argument("--vc-limit", type=int, default=5, help="Max VCs to scan per run (default 5; use 0 for all)")
    args = parser.parse_args()
    limit = len(load_vc_list()) if args.vc_limit == 0 else args.vc_limit
    run(force=args.force, vc_limit=limit)
