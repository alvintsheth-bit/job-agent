#!/usr/bin/env python3
"""Briefer: applies color coding to Jobs sheet and logs status changes."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.expanduser("~/job-agent"))
from shared.db import set_last_run
from shared.sheets import get_all_rows, update_row, set_row_color

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [briefer] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

COLOR_MAP = {
    "green": "#d9ead3",   # fit_score 8-10
    "yellow": "#fff2cc",  # fit_score 6-7
    "gray": "#f3f3f3",    # archived
    "blue": "#cfe2f3",    # interviewing
    "orange": "#fce5cd",  # applied
}


def row_color(row: dict) -> str | None:
    status = str(row.get("status", "")).lower()
    score_raw = row.get("fit_score", "")
    try:
        score = int(score_raw)
    except (ValueError, TypeError):
        score = None

    if status == "archived":
        return COLOR_MAP["gray"]
    if status == "interviewing":
        return COLOR_MAP["blue"]
    if status == "applied":
        return COLOR_MAP["orange"]
    if score is not None:
        if score >= 8:
            return COLOR_MAP["green"]
        if score >= 6:
            return COLOR_MAP["yellow"]
    return None


def run():
    log.info("Briefer starting")
    now = datetime.now(timezone.utc)
    cutoff_48h = now - timedelta(hours=48)
    cutoff_24h = now - timedelta(hours=24)

    rows = get_all_rows("Jobs")
    colored_count = 0
    status_update_count = 0

    for i, row in enumerate(rows):
        sheet_row_idx = i + 2  # header + 1-indexed

        color = row_color(row)
        if color:
            try:
                set_row_color("Jobs", sheet_row_idx, color)
                colored_count += 1
            except Exception as e:
                log.error(f"  Color set failed row {sheet_row_idx}: {e}")

        # Append status change note if status changed within last 24h
        # We detect this by checking if notes already has today's timestamp
        status = row.get("status", "")
        notes = str(row.get("notes", ""))
        ts_str = now.strftime("%Y-%m-%d %H:%M")
        status_note = f"[{ts_str}] Status → {status}"
        if status and status_note not in notes:
            date_found = str(row.get("date_found", ""))
            # Approximate: if the row was found within last 24h, append status note
            try:
                from datetime import date
                found_date = datetime.strptime(date_found, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if found_date >= cutoff_24h:
                    new_notes = f"{notes}\n{status_note}".strip()
                    update_row("Jobs", sheet_row_idx, {"notes": new_notes})
                    status_update_count += 1
            except Exception:
                pass

    log.info(
        f"Briefer {now.strftime('%Y-%m-%d %H:%M')}: "
        f"{colored_count} roles colored, {status_update_count} status updates"
    )
    set_last_run("briefer")


if __name__ == "__main__":
    run()
