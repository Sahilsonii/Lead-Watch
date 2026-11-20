"""Google Sheet sync, against a fake worksheet: never calls Google."""
import json
import os

import gsheet
import process as p


class FakeSheet:
    def __init__(self, fail_on=None):
        self.rows, self.calls, self.fail_on = [], [], fail_on

    def row_values(self, n):
        return self.rows[n - 1] if len(self.rows) >= n else []

    def append_rows(self, rows, value_input_option=None):
        self.calls.append(value_input_option)
        if len(self.calls) == self.fail_on:
            raise ConnectionError("Google said no")
        self.rows += rows


con = p.db(":memory:")
for i in range(5):
    con.execute("INSERT INTO leads(place_id, name, phone, query, raw) VALUES (?,?,?,?,?)",
                (f"p{i}", f"=Clinic {i}", f"04 388 22{i}0", "AE|dentist|Dubai", "{}"))
con.commit()

# first sync: the column header, then every lead once, as plain text (a "=" name never becomes a formula)
ws = FakeSheet()
assert gsheet.push(con, ws, chunk=2) == 5
assert ws.rows[0] == p.COLUMNS and len(ws.rows) == 6 and set(ws.calls) == {"RAW"}
assert sorted(r[0] for r in ws.rows[1:]) == [f"=Clinic {i}" for i in range(5)]
# nothing new: nothing sent, no second header
assert gsheet.push(con, ws, chunk=2) == 0 and len(ws.rows) == 6

# a new lead is appended under the existing rows
con.execute("INSERT INTO leads(place_id, name, phone, query, raw) VALUES ('p9', 'Late Clinic', '04 111', 'AE|dentist|Dubai', '{}')")
assert gsheet.push(con, ws) == 1 and ws.rows[-1][0] == "Late Clinic" and ws.rows.count(p.COLUMNS) == 1

# Google failing halfway: the chunks that went through stay marked, the rest go next time
con.execute("UPDATE leads SET gsheet_at = NULL")
ws = FakeSheet(fail_on=3)  # header + first chunk land, the second chunk fails
try:
    gsheet.push(con, ws, chunk=2)
    raise AssertionError("the failure must reach the caller")
except ConnectionError:
    pass
assert len(p.not_in_sheet(con, "gsheet_at")) == 4
ws.fail_on = None
assert gsheet.push(con, ws, chunk=2) == 4 and len(ws.rows) == 1 + 6

# not set up: sync does nothing and says why; a bad key gives a readable reason, never the key itself
os.environ.pop("GOOGLE_SHEETS_KEY", None)
os.environ.pop("GOOGLE_SHEET_ID", None)
assert gsheet.sync(con) == (0, "not set up")
os.environ["GOOGLE_SHEETS_KEY"], os.environ["GOOGLE_SHEET_ID"] = "{not json", "abc"
n, why = gsheet.sync(con)
assert n == 0 and why.startswith("Google Sheet sync failed") and "{not json" not in why, why
os.environ["GOOGLE_SHEETS_KEY"] = json.dumps({"private_key": "SECRET"})
assert "SECRET" not in gsheet.sync(con)[1]
print("all checks passed")
