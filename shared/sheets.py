from __future__ import annotations

import os
import re
import time
from typing import Any

import gspread
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

load_dotenv(os.path.expanduser("~/job-agent/config/.env"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CREDS_PATH = os.path.expanduser(
    os.environ.get("GOOGLE_CREDENTIALS_PATH", "~/job-agent/config/google_credentials.json")
)
TOKEN_PATH = os.path.expanduser("~/job-agent/config/google_token.json")
ENV_PATH = os.path.expanduser("~/job-agent/config/.env")

TAB_SCHEMAS = {
    "Jobs": [
        "job_id", "company", "role_title", "function_category", "level_match",
        "fit_score", "fit_rationale", "resume_hook", "url", "date_found",
        "source", "ats_type", "status", "applied_date", "notes",
    ],
    "Email Events": [
        "event_id", "job_id", "company", "email_date", "sender",
        "subject", "classification", "notes",
    ],
    "Scout Review": [
        "company", "careers_url", "ats_type", "vc_backer", "tier", "rationale",
    ],
    "Company List": [
        "company", "careers_url", "ats_type", "vc_backer", "tier", "date_added", "active",
    ],
}

_gc: gspread.Client | None = None
_spreadsheet: gspread.Spreadsheet | None = None

# In-memory ID counters so we never re-read the sheet to find next_id
_next_id: dict[str, int] = {}
# Cached headers per tab
_headers_cache: dict[str, list[str]] = {}


def _retry(fn, retries=5, base_delay=10):
    """Call fn(), retrying on 429 with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            if e.response.status_code == 429 and attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)
            else:
                raise


def _get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
    return creds


def _client() -> gspread.Client:
    global _gc
    if _gc is None:
        creds = _get_credentials()
        _gc = gspread.authorize(creds)
    return _gc


def _ensure_tabs(spreadsheet: gspread.Spreadsheet) -> None:
    existing = [ws.title for ws in spreadsheet.worksheets()]
    for tab_name, headers in TAB_SCHEMAS.items():
        if tab_name not in existing:
            ws = spreadsheet.add_worksheet(title=tab_name, rows=5000, cols=len(headers))
            ws.append_row(headers)
        else:
            ws = spreadsheet.worksheet(tab_name)
            current = ws.row_values(1)
            if not current:
                ws.append_row(headers)
    if "Sheet1" in existing:
        try:
            spreadsheet.del_worksheet(spreadsheet.worksheet("Sheet1"))
        except Exception:
            pass


def _write_sheet_id_to_env(sheet_id: str) -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        content = f.read()
    if f"GOOGLE_SHEET_ID={sheet_id}" in content:
        return
    new_content = re.sub(r"GOOGLE_SHEET_ID=.*", f"GOOGLE_SHEET_ID={sheet_id}", content)
    with open(ENV_PATH, "w") as f:
        f.write(new_content)


def get_or_create_spreadsheet(title: str = "Physical AI Job Search") -> gspread.Spreadsheet:
    global _spreadsheet
    if _spreadsheet is not None:
        return _spreadsheet
    gc = _client()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if sheet_id:
        _spreadsheet = gc.open_by_key(sheet_id)
    else:
        try:
            _spreadsheet = gc.open(title)
        except gspread.SpreadsheetNotFound:
            _spreadsheet = gc.create(title)
            _write_sheet_id_to_env(_spreadsheet.id)
            os.environ["GOOGLE_SHEET_ID"] = _spreadsheet.id
    _ensure_tabs(_spreadsheet)
    return _spreadsheet


def _worksheet(tab_name: str) -> gspread.Worksheet:
    ss = get_or_create_spreadsheet()
    return ss.worksheet(tab_name)


def _get_headers(tab_name: str) -> list[str]:
    if tab_name not in _headers_cache:
        ws = _worksheet(tab_name)
        _headers_cache[tab_name] = ws.row_values(1)
    return _headers_cache[tab_name]


def _get_next_id(tab_name: str, id_col: str) -> int:
    """Return next auto-increment ID, reading sheet only once per session."""
    if tab_name not in _next_id:
        ws = _worksheet(tab_name)
        records = _retry(ws.get_all_records)
        current_max = max((r.get(id_col, 0) for r in records), default=0)
        _next_id[tab_name] = current_max + 1
    val = _next_id[tab_name]
    _next_id[tab_name] += 1
    return val


def get_all_rows(tab_name: str) -> list[dict]:
    ws = _worksheet(tab_name)
    return _retry(ws.get_all_records)


def append_row(tab_name: str, row_dict: dict) -> int:
    ws = _worksheet(tab_name)
    headers = _get_headers(tab_name)
    id_col = "job_id" if tab_name == "Jobs" else "event_id" if tab_name == "Email Events" else None
    if id_col and id_col in headers and id_col not in row_dict:
        row_dict[id_col] = _get_next_id(tab_name, id_col)
    row = [row_dict.get(h, "") for h in headers]
    _retry(lambda: ws.append_row(row, value_input_option="USER_ENTERED"))
    return _next_id.get(tab_name, 0)


def batch_append_rows(tab_name: str, row_dicts: list[dict]) -> int:
    """Write many rows in a single API call. Much more efficient than append_row in a loop."""
    if not row_dicts:
        return 0
    ws = _worksheet(tab_name)
    headers = _get_headers(tab_name)
    id_col = "job_id" if tab_name == "Jobs" else "event_id" if tab_name == "Email Events" else None
    rows = []
    for row_dict in row_dicts:
        if id_col and id_col in headers and id_col not in row_dict:
            row_dict[id_col] = _get_next_id(tab_name, id_col)
        rows.append([row_dict.get(h, "") for h in headers])
    _retry(lambda: ws.append_rows(rows, value_input_option="USER_ENTERED"))
    return len(rows)


def update_row(tab_name: str, row_index: int, updates: dict) -> None:
    ws = _worksheet(tab_name)
    headers = _get_headers(tab_name)
    for col_name, value in updates.items():
        if col_name in headers:
            col_index = headers.index(col_name) + 1
            _retry(lambda ci=col_index, v=value: ws.update_cell(row_index, ci, v))


def find_row(tab_name: str, column_name: str, value: str) -> tuple[dict, int] | None:
    ws = _worksheet(tab_name)
    records = _retry(ws.get_all_records)
    for i, record in enumerate(records):
        if str(record.get(column_name, "")) == str(value):
            return record, i + 2
    return None


def set_row_color(tab_name: str, row_index: int, hex_color: str) -> None:
    ss = get_or_create_spreadsheet()
    ws = _worksheet(tab_name)
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255
    g = int(hex_color[2:4], 16) / 255
    b = int(hex_color[4:6], 16) / 255
    sheet_id = ws.id
    body = {
        "requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_index - 1,
                    "endRowIndex": row_index,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": r, "green": g, "blue": b}
                    }
                },
                "fields": "userEnteredFormat.backgroundColor",
            }
        }]
    }
    _retry(lambda: ss.batch_update(body))


def sheet_exists(title: str) -> bool:
    gc = _client()
    try:
        gc.open(title)
        return True
    except gspread.SpreadsheetNotFound:
        return False
