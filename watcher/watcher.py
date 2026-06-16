#!/usr/bin/env python3
"""Watcher: polls ATS endpoints and adds new relevant jobs to the Jobs sheet."""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

sys.path.insert(0, os.path.expanduser("~/job-agent"))
from shared.db import set_last_run
from shared.sheets import get_all_rows, append_row

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watcher] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

COMPANY_LIST = os.path.expanduser("~/job-agent/config/company_list.yaml")
PROFILE = os.path.expanduser("~/job-agent/config/profile.yaml")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job-agent/1.0)"}


def load_exclude_keywords() -> list[str]:
    with open(PROFILE) as f:
        profile = yaml.safe_load(f)
    return [kw.lower() for kw in profile.get("exclude_title_keywords", [])]


def load_companies() -> list[dict]:
    with open(COMPANY_LIST) as f:
        data = yaml.safe_load(f)
    tier1 = data.get("tier1", [])
    for c in tier1:
        c["tier"] = "tier1"
    return [c for c in tier1 if c.get("active", True)]


def is_excluded(title: str, keywords: list[str]) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in keywords)


def existing_jobs() -> set[tuple[str, str]]:
    rows = get_all_rows("Jobs")
    return {(r.get("company", ""), r.get("role_title", "")) for r in rows}


def fetch_greenhouse(company: dict) -> list[dict]:
    try:
        resp = requests.get(company["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("jobs", []):
            jobs.append({
                "company": company["company"],
                "role_title": j.get("title", ""),
                "url": j.get("absolute_url", ""),
                "jd_text": BeautifulSoup(j.get("content", ""), "lxml").get_text(" "),
                "source": "greenhouse",
                "ats_type": "greenhouse",
            })
        return jobs
    except Exception as e:
        log.error(f"Greenhouse fetch failed for {company['company']}: {e}")
        return []


def fetch_ashby(company: dict) -> list[dict]:
    try:
        resp = requests.get(company["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("jobPostings", []):
            jd_html = j.get("descriptionHtml", "")
            jobs.append({
                "company": company["company"],
                "role_title": j.get("title", ""),
                "url": j.get("jobUrl", ""),
                "jd_text": BeautifulSoup(jd_html, "lxml").get_text(" ") if jd_html else "",
                "source": "ashby",
                "ats_type": "ashby",
            })
        return jobs
    except Exception as e:
        log.error(f"Ashby fetch failed for {company['company']}: {e}")
        return []


def fetch_lever(company: dict) -> list[dict]:
    try:
        resp = requests.get(company["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data:
            jobs.append({
                "company": company["company"],
                "role_title": j.get("text", ""),
                "url": j.get("hostedUrl", ""),
                "jd_text": j.get("descriptionPlain", ""),
                "source": "lever",
                "ats_type": "lever",
            })
        return jobs
    except Exception as e:
        log.error(f"Lever fetch failed for {company['company']}: {e}")
        return []


def fetch_workday(company: dict) -> list[dict]:
    try:
        resp = requests.get(company["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []
        # Generic Workday parser — extract job link elements
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            title = tag.get_text(strip=True)
            if title and ("job" in href.lower() or "posting" in href.lower()):
                full_url = href if href.startswith("http") else company["url"].rstrip("/") + href
                jobs.append({
                    "company": company["company"],
                    "role_title": title,
                    "url": full_url,
                    "jd_text": "",
                    "source": "workday",
                    "ats_type": "workday",
                })
        return jobs
    except Exception as e:
        log.error(f"Workday fetch failed for {company['company']}: {e}")
        return []


def fetch_custom(company: dict) -> list[dict]:
    try:
        resp = requests.get(company["url"], headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        jobs = []
        for tag in soup.find_all("a", href=True):
            title = tag.get_text(strip=True)
            href = tag["href"]
            if len(title) > 10 and any(
                kw in href.lower() or kw in title.lower()
                for kw in ["job", "career", "position", "role", "opening"]
            ):
                full_url = href if href.startswith("http") else company["url"].rstrip("/") + "/" + href.lstrip("/")
                jobs.append({
                    "company": company["company"],
                    "role_title": title,
                    "url": full_url,
                    "jd_text": "",
                    "source": "custom",
                    "ats_type": "custom",
                })
        return jobs
    except Exception as e:
        log.error(f"Custom fetch failed for {company['company']}: {e}")
        return []


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "custom": fetch_custom,
}


def run():
    log.info("Watcher starting")
    exclude_kws = load_exclude_keywords()
    companies = load_companies()
    seen = existing_jobs()
    today = date.today().isoformat()
    new_count = 0

    for company in companies:
        ats = company.get("ats", "custom")
        fetcher = FETCHERS.get(ats, fetch_custom)
        jobs = fetcher(company)
        log.info(f"{company['company']}: fetched {len(jobs)} raw listings via {ats}")

        for job in jobs:
            title = job["role_title"].strip()
            if not title:
                continue
            if is_excluded(title, exclude_kws):
                log.debug(f"  SKIP (excluded): {title}")
                continue
            key = (company["company"], title)
            if key in seen:
                log.debug(f"  SKIP (exists): {title}")
                continue
            row = {
                "company": job["company"],
                "role_title": title,
                "url": job["url"],
                "date_found": today,
                "source": job["source"],
                "ats_type": job["ats_type"],
                "status": "new",
                "fit_score": "",
                "function_category": "",
                "level_match": "",
                "fit_rationale": "",
                "resume_hook": "",
                "applied_date": "",
                "notes": "",
            }
            try:
                append_row("Jobs", row)
                seen.add(key)
                new_count += 1
                log.info(f"  ADDED: {title}")
            except Exception as e:
                log.error(f"  Sheet write failed for '{title}': {e}")

    log.info(f"Watcher done: {new_count} new jobs added")
    set_last_run("watcher")


if __name__ == "__main__":
    run()
