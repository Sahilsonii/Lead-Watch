"""Self-check for the parsing, dedupe and export rules. Run: python pipeline/test_process.py"""
import process as p

# phone normalization: local formats need the country hint, landlines are dropped
assert p.to_e164("050 123 4567", "AE") == "+971501234567"
assert p.to_e164("+44 7911 123456", None) == "+447911123456"
assert p.to_e164("04 123 4567", "AE") is None          # Dubai landline
assert p.to_e164("", "AE") is None and p.to_e164(None, None) is None

# display format: each country's own grouping, with the country code, whatever spacing Google gave
assert p.pretty_phone("04 566 2262", "AE") == "+971 4 566 2262"
assert p.pretty_phone("045662262", "AE") == "+971 4 566 2262"
assert p.pretty_phone("+971501234567", None) == "+971 50 123 4567"
assert p.pretty_phone("020 7946 0958", "GB") == "+44 20 7946 0958"
assert p.pretty_phone("+1 416-555-0199", "CA") == "+1 416-555-0199"   # North America's own style
assert p.pretty_phone("12", "AE") == "12" and p.pretty_phone(None, "AE") == ""  # unparseable: left as it was

# search ids carry country, sector and location; old ids were just the country
assert p.split_input("AE|real estate agency|Dubai") == ("AE", "real estate agency", "Dubai")
assert p.split_input("AE") == ("AE", "", "")

# junk and malformed emails never reach DNS
assert p.verify_email("logo@2x.png") == "invalid"
assert p.verify_email("john@example.com") == "invalid"
assert p.verify_email("not-an-email") == "invalid"
assert p.mx_host("w3.org")                                     # a real mail domain...
assert p.verify_email("team@w3.org") == "invalid"              # ...but w3.org addresses come from site code
assert p.verify_email("info@gmail.com") == "mx_ok"     # real MX lookup (needs internet)
assert p.verify_email("x@no-such-domain-zz9q.com") == "invalid"

# own-domain email wins over a free-mail one
assert p.best_email(["someone@gmail.com", "hello@google.com"], "https://www.google.com/")[0] == "hello@google.com"

# dedupe: same email or same phone across listings (chains) is kept once
rows = [{"email": "a@x.com", "phone_e164": "+971501234567", "phone": ""},
        {"email": "a@x.com", "phone_e164": None, "phone": "04 1"},          # same email
        {"email": None, "phone_e164": "+971501234567", "phone": ""},         # same mobile
        {"email": "b@y.com", "phone_e164": None, "phone": ""}]
assert p.dedupe_contacts(rows) == [rows[0], rows[3]]

# owner name: only from a "Dr." title or a personal-looking mailbox, never a generic one
assert p.owner_of({"title": "Dr. Paul's Dental Clinic Dubai"}, None) == ("Dr. Paul", "Business name")
assert p.owner_of({"title": "Smile Clinic"}, "sarah.jones@smileclinic.ae") == ("Sarah Jones", "Email address (guess)")
assert p.owner_of({"title": "Smile Clinic"}, "info@smileclinic.ae") == ("", "Not found")
assert p.owner_of({"title": "Smile Clinic"}, "smile@smileclinic.ae") == ("", "Not found")

# authenticity verdict
lead = {"website": "https://smile.ae", "reviews": 120, "email": "hi@smile.ae", "email_status": "mx_ok",
        "phone_e164": "+971501234567", "phone": "050 123 4567"}
assert p.verdict({"owner": {"name": "x"}}, lead).startswith("Verified (owner-claimed, 120 reviews, website, email on own domain")
assert p.verdict({"status": "Permanently closed"}, lead) == "Closed"
assert p.verdict({}, {**lead, "website": "", "reviews": 2, "email_status": None}) == "Weak (valid phone)"

# proportional verdict: share of the signals that apply to this lead
assert p.verdict({"owner": 1}, {**lead, "reviews": 2, "email_status": None}).startswith("Likely genuine")  # 3 of 5
crawled = {**lead, "reviews": 2, "site_status": "ok", "site_matches": 1}
assert p.verdict({}, crawled).startswith("Verified"), p.verdict({}, crawled)                               # 4 of 6

# owner priority: website team page > "Dr." in the business name > named email on the website
dr = {"title": "Dr. Paul's Dental Clinic"}
assert p.lead_owner(dr, {"email": None, "owner_name": "Lina Haddad", "owner_source": "Website team page"}) \
    == ("Lina Haddad", "Website team page")
assert p.lead_owner(dr, {"email": None, "owner_name": "Jane Smith", "owner_source": "Website email link"}) \
    == ("Dr. Paul", "Business name")

# enrichment storage, export columns and the crawl queue, on a throwaway database
import asyncio, json, threading
import enrich
con = p.db(":memory:")
for pid, site in (("a", "https://smile.ae"), ("b", "https://www.facebook.com/smile")):
    con.execute("INSERT INTO leads(place_id, name, website, phone, query, raw) VALUES (?,?,?,?,?,?)",
                (pid, "Smile Clinic", site, "", "AE|dentist|Dubai", json.dumps({"title": "Smile Clinic"})))
found = {"status": "ok", "emails": {}, "phones": ["+971501234567"], "social": ["https://instagram.com/smile"],
         "owner": ("SMILE DENTAL LTD", "Owner", "Website team page"), "matches": True, "pages": 2}
p.save_enrichment(con, "a", found)
a = con.execute("SELECT owner_name, phone_e164, social_links, site_status, enriched_at FROM leads WHERE place_id='a'").fetchone()
assert a[0] is None and a[1] == "+971501234567" and a[2] == "https://instagram.com/smile" and a[3] == "ok" and a[4], a
# a site with no owner never erases a name found earlier (LinkedIn)
con.execute("UPDATE leads SET owner_name='Sara Ali', owner_role='Founder', owner_source='LinkedIn search result' WHERE place_id='a'")
p.save_enrichment(con, "a", {**found, "owner": None})
assert con.execute("SELECT owner_name, owner_source FROM leads WHERE place_id='a'").fetchone() == ("Sara Ali", "LinkedIn search result")

con.row_factory = p.sqlite3.Row
row = p.lead_row(con.execute("SELECT * FROM leads WHERE place_id='b'").fetchone())
assert len(p.COLUMNS) == 20 and len(row) == 20
assert p.COLUMNS[:18] == ["Business Name", "Owner Name", "Owner Source", "Address/Location", "Postcode", "City", "Region",
                          "Business Sector", "Category", "Phone", "Email", "WhatsApp", "Website",
                          "Total Google Reviews", "Rating", "Short Brief", "Date Added", "Authenticity Verdict"]
cols = dict(zip(p.COLUMNS, row))
a_row = dict(zip(p.COLUMNS, p.lead_row(con.execute("SELECT * FROM leads WHERE place_id='a'").fetchone())))

# the Excel history: the column header once, then per run its date and time, a blank row and its new leads
import openpyxl
import tempfile
from pathlib import Path
p.DATA = Path(tempfile.mkdtemp())  # never the real data/ folder
xlsx = p.DATA / "check.xlsx"
blank = [""] * (len(p.COLUMNS) - 2)
assert p.append_xlsx(xlsx, "2026-11-20 04:05", [["=HYPERLINK(\"http://x\")", 4.5] + blank])
assert p.append_xlsx(xlsx, None, [["Second", 1] + blank])  # same run: its block goes on
assert p.append_xlsx(xlsx, "2026-11-20 09:00", [["Third", 2] + blank])
sheet = openpyxl.load_workbook(xlsx).active
assert [c.value for c in sheet[1]] == p.COLUMNS and sheet.freeze_panes == "A2"
assert [sheet.cell(r, 1).value for r in range(2, 10)] ==     ["2026-11-20 04:05", None, '=HYPERLINK("http://x")', "Second", None, "2026-11-20 09:00", None, "Third"]
assert sheet["A4"].data_type == "s" and sheet["B4"].value == 4.5  # web text never becomes a live formula

# export: every lead goes to the CSV; the sheet gets each lead once, at most `limit` per call, the rest next time
disk = p.db()  # export opens DATA/leads.db: copy this test's leads there
for r in con.execute("SELECT * FROM leads").fetchall():
    disk.execute(f"INSERT INTO leads({', '.join(r.keys())}) VALUES ({', '.join('?' * len(r))})", tuple(r))
disk.execute("INSERT INTO leads(place_id, name, phone, phone_e164, query, raw) VALUES "
             "('z', 'Zed Clinic', '+971 50 765 4321', '+971507654321', 'AE|dentist|Dubai', '{}')")
disk.commit()
waiting = len(p.not_in_sheet(disk))
assert waiting >= 2, waiting
assert p.export("2026-11-20 10:00", limit=1) == 1 and len(p.not_in_sheet(disk)) == waiting - 1
assert p.export("2026-11-20 10:00") == waiting - 1 and not p.not_in_sheet(disk)
assert p.export("2026-11-20 11:00") == 0  # nothing new: no empty block for this run
sheet = openpyxl.load_workbook(p.DATA / "leads.xlsx").active
assert [sheet.cell(r, 1).value for r in range(2, 4)] == ["2026-11-20 10:00", None]
assert sheet.max_row == 3 + waiting and "2026-11-20 11:00" not in [c.value for c in sheet["A"]]
assert a_row["Phone"] == "+971 50 123 4567" and a_row["WhatsApp"] == "+971 50 123 4567", a_row
assert cols["Website"] == "" and "https://www.facebook.com/smile" in cols["Social Links"] and cols["Sources"] == "google_maps"
con.row_factory = None

con.execute("UPDATE leads SET enriched_at = NULL")
calls = []


async def fake_crawl(client, url, business, phone, region, suffixes):
    calls.append(url)
    return {**found, "owner": ("Sara Ali", "Founder", "Website structured data"), "phones": []}
enrich.crawl_site = fake_crawl
stats = asyncio.run(enrich.enrich_pending(con, 10, threading.Event()))
assert stats == {"crawled": 2, "names": 2, "blocked": 0}, stats
assert con.execute("SELECT COUNT(*) FROM leads WHERE enriched_at IS NULL").fetchone()[0] == 0
assert asyncio.run(enrich.enrich_pending(con, 10, threading.Event()))["crawled"] == 0  # each site crawled once
# a later, weaker name never replaces a stronger one
assert not p.save_enrichment(con, "a", {**found, "owner": ("Info Desk", None, "Website email link"), "phones": []})
assert con.execute("SELECT owner_name FROM leads WHERE place_id = 'a'").fetchone()[0] == "Sara Ali"
assert p.outranks("Companies House owner", None) and not p.outranks("Email address (guess)", "Business name")

# a chain: two leads on one website -> the site is read once, both leads get the result
con.execute("UPDATE leads SET enriched_at = NULL, website = 'https://smile.ae'")
con.execute("UPDATE leads SET website = 'www.smile.ae/' WHERE place_id = 'b'")
calls.clear()
stats = asyncio.run(enrich.enrich_pending(con, 10, threading.Event()))
assert len(calls) == 1 and stats["crawled"] == 2 and stats["names"] == 2, (calls, stats)
con.execute("UPDATE leads SET website = 'https://www.facebook.com/smile' WHERE place_id = 'b'")

# live run: one site that crashes the parser must not stop the others (or the runner thread)
con.execute("UPDATE leads SET enriched_at = NULL")


async def flaky_crawl(client, url, business, phone, region, suffixes):
    if "facebook" in url:
        raise TypeError("bad page")
    return found
enrich.crawl_site = flaky_crawl
stats = asyncio.run(enrich.enrich_pending(con, 10, threading.Event()))
assert stats["crawled"] == 2, stats
assert con.execute("SELECT site_status FROM leads WHERE place_id='b'").fetchone()[0] == "error"  # not retried forever
enrich.crawl_site = fake_crawl
stop = threading.Event(); stop.set()
con.execute("UPDATE leads SET enriched_at = NULL")
assert asyncio.run(enrich.enrich_pending(con, 10, stop))["crawled"] == 0              # Stop is honored
print("all checks passed")
