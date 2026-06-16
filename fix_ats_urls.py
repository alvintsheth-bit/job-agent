#!/usr/bin/env python3
"""
Fix ATS API URLs for Scout Review companies by trying common slug patterns.
Reports results before doing any web search.
"""

from __future__ import annotations

import os
import re
import sys
import time
import requests

sys.path.insert(0, os.path.expanduser("~/job-agent"))
from dotenv import load_dotenv
from shared.sheets import get_all_rows, get_or_create_spreadsheet

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-agent/1.0)"}

ATS_PATTERNS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "ashby":      "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "lever":      "https://api.lever.co/v0/postings/{slug}?mode=json",
}

API_DOMAINS = ["boards-api.greenhouse.io", "api.ashbyhq.com", "api.lever.co", "myworkdayjobs.com"]


def is_api_url(url: str) -> bool:
    return any(d in url for d in API_DOMAINS)


def slugify_variants(name: str) -> list[str]:
    """Generate slug candidates from a company name."""
    # Strip common suffixes
    name = re.sub(r'\s*(inc\.?|llc\.?|corp\.?|ltd\.?|technologies|robotics|aerospace|systems|aviation|defense|autonomous|autonomy|ai)\s*$', '', name, flags=re.IGNORECASE).strip()
    # Also try with common words
    raw = name.lower()
    no_spaces = re.sub(r'[^a-z0-9]', '', raw)
    hyphenated = re.sub(r'[^a-z0-9]+', '-', raw).strip('-')
    # First word only
    first_word = re.sub(r'[^a-z0-9]', '', raw.split()[0]) if raw.split() else ''

    variants = []
    for v in [hyphenated, no_spaces, first_word]:
        if v and v not in variants:
            variants.append(v)
    return variants


def try_ats(ats_type: str, slug: str) -> bool:
    """Return True if the ATS API endpoint returns valid JSON with jobs."""
    pattern = ATS_PATTERNS.get(ats_type)
    if not pattern:
        return False
    url = pattern.format(slug=slug)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        # Greenhouse: has "jobs" key; Ashby: has "jobPostings" or "jobs"; Lever: is a list
        if ats_type == "greenhouse":
            return "jobs" in data
        elif ats_type == "ashby":
            return "jobPostings" in data or "jobs" in data
        elif ats_type == "lever":
            return isinstance(data, list)
    except Exception:
        pass
    return False


def find_api_url(company: str, ats_type: str) -> tuple[str, str] | None:
    """Try slug variants and return (api_url, slug) if found, else None."""
    if ats_type not in ATS_PATTERNS:
        return None
    for slug in slugify_variants(company):
        if try_ats(ats_type, slug):
            url = ATS_PATTERNS[ats_type].format(slug=slug)
            return url, slug
        time.sleep(0.2)
    return None


def main():
    print("Loading Scout Review tab...")
    rows = get_all_rows("Scout Review")

    needs_fix = []
    already_api = []
    custom = []

    for r in rows:
        name = r.get("company", "").strip()
        url = r.get("careers_url", "").strip()
        ats = r.get("ats_type", "custom").strip().lower()
        if not name or not url:
            continue
        if ats == "custom" or ats not in ATS_PATTERNS:
            custom.append(name)
        elif is_api_url(url):
            already_api.append(name)
        else:
            needs_fix.append({"company": name, "url": url, "ats": ats, "row": r})

    print(f"\nScout Review breakdown:")
    print(f"  Already have API URL: {len(already_api)}")
    print(f"  Custom ATS (scrape only): {len(custom)}")
    print(f"  Need API URL fix: {len(needs_fix)}")
    print(f"\nTrying slug patterns for {len(needs_fix)} companies (free)...\n")

    fixed = []
    not_found = []

    for entry in needs_fix:
        name = entry["company"]
        ats = entry["ats"]
        result = find_api_url(name, ats)
        if result:
            api_url, slug = result
            fixed.append({"company": name, "ats": ats, "slug": slug, "url": api_url})
            print(f"  ✓ {name} → {slug} ({ats})")
        else:
            not_found.append(entry)
            print(f"  ✗ {name} ({ats}) — no slug match")

    print(f"\n--- Results ---")
    print(f"Fixed via slug guessing: {len(fixed)}")
    print(f"Still need web search:   {len(not_found)}")
    if not_found:
        print(f"\nCompanies needing web search:")
        for e in not_found:
            print(f"  - {e['company']} ({e['ats']})")

    if fixed:
        print(f"\nUpdating {len(fixed)} rows in Scout Review...")
        ss = get_or_create_spreadsheet()
        ws = ss.worksheet("Scout Review")
        all_rows = ws.get_all_records()
        headers = ws.row_values(1)
        url_col = headers.index("careers_url") + 1

        for fix in fixed:
            for i, row in enumerate(all_rows):
                if row.get("company", "").strip() == fix["company"]:
                    ws.update_cell(i + 2, url_col, fix["url"])
                    time.sleep(0.5)
                    break

        print("Sheet updated.")


if __name__ == "__main__":
    main()
