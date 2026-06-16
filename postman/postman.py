#!/usr/bin/env python3
"""Postman: reads Gmail for job application emails and updates Jobs sheet."""

from __future__ import annotations

import base64
import logging
import json
import os
import sys
from datetime import date

import anthropic
from dotenv import load_dotenv
from googleapiclient.discovery import build

sys.path.insert(0, os.path.expanduser("~/job-agent"))
from shared.db import set_last_run
from shared.sheets import get_all_rows, append_row, update_row, find_row

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [postman] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

STATUS_MAP = {
    "application_ack": "applied",
    "interview_request": "interviewing",
    "rejection": "rejected",
    "offer": "offer",
}

CLASSIFY_PROMPT = """\
Classify this job application email. Return JSON only — no prose, no markdown fences:
{{
  "classification": "<application_ack|interview_request|rejection|offer|unknown>",
  "company_guess": "<company name or null>"
}}
Subject: {subject}
Snippet: {snippet}\
"""


def _gmail_service():
    # Reuse the same credentials from shared/sheets.py
    import pickle
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    TOKEN_PATH = os.path.expanduser("~/job-agent/config/google_token.json")
    creds = None
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "No valid Gmail credentials. Run watcher or sheets first to authenticate."
            )
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
    return build("gmail", "v1", credentials=creds)


def build_domain_query(company_names: list[str]) -> str:
    domains = []
    for name in company_names:
        slug = name.lower().replace(" ", "").replace(".", "").replace(",", "")[:20]
        domains.append(slug)
    domain_str = " OR ".join(f"from:{d}" for d in domains[:30])  # Gmail query limit
    return f"({domain_str}) newer_than:1d"


def search_gmail(service, query: str) -> list[dict]:
    result = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = result.get("messages", [])
    emails = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        emails.append({
            "id": msg_ref["id"],
            "sender": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
        })
    return emails


def classify_email(client: anthropic.Anthropic, email: dict) -> dict:
    prompt = CLASSIFY_PROMPT.format(
        subject=email["subject"][:200],
        snippet=email["snippet"][:500],
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def run():
    log.info("Postman starting")
    service = _gmail_service()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    company_rows = get_all_rows("Company List")
    company_names = [r.get("company", "") for r in company_rows if r.get("company")]

    domain_query = build_domain_query(company_names)
    subject_query = (
        "subject:(application received OR thank you for applying OR we received your "
        "OR interview OR unfortunately OR move forward) newer_than:1d"
    )

    emails_by_id: dict[str, dict] = {}
    for query in [domain_query, subject_query]:
        for email in search_gmail(service, query):
            emails_by_id[email["id"]] = email

    log.info(f"Found {len(emails_by_id)} unique emails")

    today = date.today().isoformat()
    existing_events = get_all_rows("Email Events")
    existing_event_ids = {r.get("event_id", "") for r in existing_events}

    for email in emails_by_id.values():
        if email["id"] in existing_event_ids:
            continue
        try:
            classification = classify_email(client, email)
            cls = classification.get("classification", "unknown")
            company_guess = classification.get("company_guess")
            log.info(f"  {email['subject'][:60]} → {cls} ({company_guess})")

            # Append to Email Events tab
            event_row = {
                "job_id": "",
                "company": company_guess or "",
                "email_date": today,
                "sender": email["sender"],
                "subject": email["subject"],
                "classification": cls,
                "notes": email["snippet"][:200],
            }
            append_row("Email Events", event_row)

            # Update Jobs status if matched
            if company_guess and cls in STATUS_MAP:
                result = find_row("Jobs", "company", company_guess)
                if result:
                    job_row, row_idx = result
                    new_status = STATUS_MAP[cls]
                    update_row("Jobs", row_idx, {"status": new_status})
                    log.info(f"  Updated {company_guess} job status → {new_status}")
                else:
                    log.info(f"  No Jobs row found for company: {company_guess}")
            elif cls == "unknown":
                log.info(f"  Unknown classification — logged only")

        except Exception as e:
            log.error(f"  Failed to process email '{email.get('subject', '')}': {e}")

    log.info("Postman done")
    set_last_run("postman")


if __name__ == "__main__":
    run()
