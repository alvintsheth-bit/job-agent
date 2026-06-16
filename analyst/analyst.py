#!/usr/bin/env python3
"""Analyst: scores unscored Jobs sheet rows using Claude API."""

from __future__ import annotations

import json
import logging
import os
import sys

import anthropic
import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.expanduser("~/job-agent"))
from shared.db import set_last_run
from shared.resume import parse_resume
from shared.sheets import get_all_rows, update_row, find_row

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [analyst] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

PROFILE_PATH = os.path.expanduser("~/job-agent/config/profile.yaml")

SYSTEM_PROMPT_TEMPLATE = """\
You are a job fit analyst. Evaluate this job posting against the candidate's \
resume and targeting preferences. Return JSON only — no prose, no markdown fences.

Candidate resume:
{resume_text}

Targeting preferences:
{profile_yaml}

Scoring guide:
- 8-10: Strong match on function + industry + seniority. Resume has direct experience to cite.
- 6-7: Good match on 2 of 3 dimensions. Worth reviewing.
- 4-5: Partial match. Flag mismatch specifically.
- 1-3: Poor fit. Archive.

Apply level_notes: do not hard-exclude Manager-level roles at companies under 200 employees. \
Flag as level_match='under' but still score on function and industry fit.

Return format:
{{
  "fit_score": <integer 1-10>,
  "function_category": <string from target_functions or "Other">,
  "level_match": <"under"|"at"|"over">,
  "fit_rationale": <2-3 sentences citing specific resume experience vs JD requirements>,
  "resume_hook": <most relevant resume section, e.g. "Trackonomy enterprise fleet accounts">
}}\
"""


def load_profile() -> str:
    with open(PROFILE_PATH) as f:
        return f.read()


def score_job(client: anthropic.Anthropic, resume_text: str, profile_yaml: str,
              company: str, title: str, jd_text: str) -> dict:
    system = SYSTEM_PROMPT_TEMPLATE.format(
        resume_text=resume_text,
        profile_yaml=profile_yaml,
    )
    user_msg = f"Company: {company}\nTitle: {title}\nJob Description: {jd_text}"
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    # Strip markdown fences if model adds them despite instructions
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def status_from_score(score: int) -> str:
    if score >= 7:
        return "review"
    if score >= 5:
        return "maybe"
    return "archived"


def run():
    log.info("Analyst starting")
    resume_text = parse_resume()
    log.info(f"Resume loaded: {len(resume_text)} chars")
    profile_yaml = load_profile()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    rows = get_all_rows("Jobs")
    unscored = [
        (i, r) for i, r in enumerate(rows)
        if str(r.get("fit_score", "")).strip() == ""
    ]
    log.info(f"{len(unscored)} unscored rows to process")

    for list_idx, row in unscored:
        sheet_row_idx = list_idx + 2  # header + 1-indexed
        company = row.get("company", "")
        title = row.get("role_title", "")
        jd_text = row.get("notes", "")  # JD text stored in notes if available
        # Also try url field as context
        url = row.get("url", "")
        log.info(f"  Scoring: {company} — {title}")
        try:
            result = score_job(client, resume_text, profile_yaml, company, title,
                               jd_text or f"URL: {url}")
            score = int(result.get("fit_score", -1))
            updates = {
                "fit_score": score,
                "function_category": result.get("function_category", ""),
                "level_match": result.get("level_match", ""),
                "fit_rationale": result.get("fit_rationale", ""),
                "resume_hook": result.get("resume_hook", ""),
                "status": status_from_score(score),
            }
            update_row("Jobs", sheet_row_idx, updates)
            log.info(f"    → score={score}, status={updates['status']}")
        except json.JSONDecodeError as e:
            log.error(f"    JSON parse failed for '{title}': {e}")
            update_row("Jobs", sheet_row_idx, {"fit_score": -1})
        except Exception as e:
            log.error(f"    Failed for '{title}': {e}")

    log.info("Analyst done")
    set_last_run("analyst")


if __name__ == "__main__":
    run()
