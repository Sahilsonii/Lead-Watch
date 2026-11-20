"""Send new leads to a shared Google Sheet, after the CSV and Excel export.

Set up once (README, "Google Sheet"): GOOGLE_SHEETS_KEY holds the service account's JSON key (the text, in an
environment variable, never a file in the project) and GOOGLE_SHEET_ID the sheet's id from its link. Each lead is
sent once (gsheet_at), one row per contact, the same 21 columns as leads.csv. Unset: nothing happens.
"""
import json
import os
import time

import process as p

# shortcut: a Google Sheet holds 10 million cells, about 470,000 leads of 21 columns; past that, start a new sheet
CHUNK = 5000  # rows per request: well under Google's request size limit, a few requests per minute at most


def push(con, ws, chunk=CHUNK):
    """Append the leads not yet in the sheet to worksheet `ws`, the column header first when it is empty.
    Each chunk is marked sent as soon as Google accepts it, so a failure halfway loses nothing and repeats
    nothing. Returns the number of leads sent; Google's errors reach the caller."""
    new = p.not_in_sheet(con, "gsheet_at")
    if not new:
        return 0
    if not ws.row_values(1):
        ws.append_rows([p.COLUMNS], value_input_option="RAW")
    for i in range(0, len(new), chunk):
        part = new[i:i + chunk]
        ws.append_rows([p.lead_row(r) for r in part], value_input_option="RAW")  # RAW: "=..." stays text
        con.executemany("UPDATE leads SET gsheet_at = ? WHERE place_id = ?",
                        [(time.strftime("%Y-%m-%d %H:%M"), r["place_id"]) for r in part])
        con.commit()
    return len(new)


def sync(con):
    """(leads sent, what happened). Never raises: a failed sync leaves the leads unsent for the next export."""
    key, sheet_id = os.environ.get("GOOGLE_SHEETS_KEY"), os.environ.get("GOOGLE_SHEET_ID")
    if not key or not sheet_id:
        return 0, "not set up"
    try:
        import gspread
        info = json.loads(key)
        client = gspread.service_account_from_dict(info)
        try:
            ws = client.open_by_key(sheet_id).sheet1
        except (PermissionError, gspread.SpreadsheetNotFound):
            return 0, (f"Google Sheet sync failed: share the sheet with {info.get('client_email')} as Editor, "
                       "and check GOOGLE_SHEET_ID")
        n = push(con, ws)
    except Exception as e:  # never str(e) of the key parse: the message must not echo the key
        detail = type(e).__name__ if isinstance(e, (ValueError, KeyError)) else f"{type(e).__name__}: {str(e)[:200]}"
        return 0, f"Google Sheet sync failed ({detail}); the leads go next time"
    return n, f"{n} new leads sent to the Google Sheet"
