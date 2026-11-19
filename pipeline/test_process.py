"""Self-check for the parsing, dedupe and export rules. Run: python pipeline/test_process.py"""
import process as p

# phone normalization: local formats need the country hint, landlines are dropped
assert p.to_e164("050 123 4567", "AE") == "+971501234567"
assert p.to_e164("+44 7911 123456", None) == "+447911123456"
assert p.to_e164("04 123 4567", "AE") is None          # Dubai landline
assert p.to_e164("", "AE") is None and p.to_e164(None, None) is None

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

con.row_factory = p.sqlite3.Row
row = p.lead_row(con.execute("SELECT * FROM leads WHERE place_id='b'").fetchone())
assert len(p.COLUMNS) == 20 and len(row) == 20
assert p.COLUMNS[:18] == ["Business Name", "Owner Name", "Owner Source", "Address/Location", "Postcode", "City", "Region",
                          "Business Sector", "Category", "Phone", "Email", "WhatsApp", "Website",
                          "Total Google Reviews", "Rating", "Short Brief", "Date Added", "Authenticity Verdict"]
cols = dict(zip(p.COLUMNS, row))
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
stop = threading.Event(); stop.set()
con.execute("UPDATE leads SET enriched_at = NULL")
assert asyncio.run(enrich.enrich_pending(con, 10, stop))["crawled"] == 0              # Stop is honored
print("all checks passed")
