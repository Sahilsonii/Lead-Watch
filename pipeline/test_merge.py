"""Self-check for cross-source matching: one lead per business, whichever source finds it first.
Run: python pipeline/test_merge.py"""
import json
import tempfile
from pathlib import Path

import process as p

p.DATA = Path(tempfile.mkdtemp())
p._mx.update({"smile.ae": "mx.smile.ae", "lina.ae": "mx.lina.ae"})  # no DNS in this test
con = p.db()


def google(place_id, title, phone="", site="", lat=25.2000, lon=55.2700, emails=()):
    return {"place_id": place_id, "title": title, "category": "Dentist", "address": "Dubai", "phone": phone,
            "web_site": site, "emails": list(emails), "latitude": lat, "longitude": lon,
            "input_id": "AE|dentist|Dubai", "complete_address": {"country": "AE"}}


def osm(sid, name, phone="", site="", lat=25.2000, lon=55.2700, emails=()):
    return {"source_id": sid, "title": name, "category": "dentist", "address": "", "phone": phone, "web_site": site,
            "emails": list(emails), "latitude": lat, "longitude": lon, "input_id": "AE|dentist|Dubai",
            "link": f"https://www.openstreetmap.org/{sid}"}


def load(*entries):
    f = p.DATA / "raw.json"
    f.write_text("\n".join(json.dumps(e) for e in entries))
    return p.ingest(f)


def lead(place_id):
    con.row_factory = __import__("sqlite3").Row
    r = con.execute("SELECT * FROM leads WHERE place_id = ?", (place_id,)).fetchone()
    con.row_factory = None
    return r


count = lambda: con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

# country only: Google answers a UK search with Indian places when it ignores the map; those never become leads
pune = {**google("g-pune", "Pune Carpenter"), "complete_address": {"country": "IN"}}
assert load(pune, google("g-ok", "Dubai Dental")) == (1, 0) and lead("g-pune") is None and lead("g-ok")
# no address country (a service-area business): the phone's country decides; with neither, it is dropped
svc = {**google("g-svc", "Mobile Joiner", phone="+971 50 999 8877"), "complete_address": {"country": ""}}
nothing = {**google("g-none", "No Info"), "complete_address": {}}
assert load(svc, nothing) == (1, 0) and lead("g-svc") and lead("g-none") is None
con.execute("DELETE FROM leads WHERE place_id IN ('g-ok', 'g-svc')"); con.commit()

# keys: any valid phone (landlines too, not only mobiles) and the website's own domain
assert p.phone_key("04 566 2262", "AE") == "+97145662262" and p.phone_key("12", "AE") is None
assert p.site_domain("https://www.Smile.ae/contact") == "smile.ae" and p.site_domain("smile.ae") == "smile.ae"
assert p.site_domain("https://facebook.com/smile") is None and p.site_domain("") is None

# Google first, then OSM: the same landline 40 m away merges; empty fields fill, Google's values stay
load(google("g1", "Smile Dental Clinic", phone="04 566 2262", site="https://smile.ae"))
assert p.ingest_source(con, "osm", [osm("node/1", "Smile Dental", phone="+971 4 566 2262",
                                        lat=25.2003, emails=["hello@smile.ae"])]) == (0, 1)
g1 = lead("g1")
assert g1["sources"] == "google_maps osm" and g1["name"] == "Smile Dental Clinic" and g1["website"] == "https://smile.ae"
assert g1["email"] == "hello@smile.ae" and g1["email_status"] == "mx_ok" and count() == 1
assert con.execute("SELECT lead_id FROM lead_sources WHERE source = 'osm'").fetchone()[0] == "g1"

# the same OSM element again is not a new lead
assert p.ingest_source(con, "osm", [osm("node/1", "Smile Dental", phone="+971 4 566 2262")]) == (0, 0)

# a chain: same call-center number and website, 5 km away -> its own lead
assert p.ingest_source(con, "osm", [osm("node/2", "Smile Dental Marina", phone="04 566 2262",
                                        site="https://smile.ae", lat=25.2450)]) == (1, 0)
assert lead("osm:node/2")["sources"] == "osm" and count() == 2

# live run: two clinics in one building share a reception number, but each has its own website -> two leads
assert p.ingest_source(con, "osm", [osm("node/20", "Dental Zone", phone="04 338 3100", site="https://www.dentalzone.tv/",
                                        lat=25.6, lon=55.6)]) == (1, 0)
assert p.ingest_source(con, "osm", [osm("node/21", "Medicazone", phone="+971 4 338 3100", site="https://medicazoneclinic.com/",
                                        lat=25.6, lon=55.6)]) == (1, 0)

# one brand's website, two hotels 300 m apart with their own numbers -> two leads; a booking platform is no key at all
assert p.ingest_source(con, "osm", [osm("node/30", "Burj Al Arab", phone="04 301 7777", site="https://www.jumeirah.com/burj",
                                        lat=25.141, lon=55.185)]) == (1, 0)
assert p.ingest_source(con, "osm", [osm("node/31", "Jumeirah Beach Hotel", phone="04 348 0000", site="https://jumeirah.com/jbh",
                                        lat=25.1437, lon=55.1855)]) == (1, 0)
assert p.site_domain("https://www.zomato.com/dubai/x") is None and p.site_domain("https://sites.google.com/view/x") is None

# website domain match (www vs bare), no phone on either side
load(google("g2", "Lina Clinic", site="https://www.lina.ae/", lat=25.1000, lon=55.1000))
assert p.ingest_source(con, "osm", [osm("node/3", "Dr Lina", site="lina.ae", lat=25.1001, lon=55.1000)]) == (0, 1)

# a Facebook "website" is never a match key
load(google("g3", "Pearl Spa", site="https://facebook.com/pearl", lat=25.3000, lon=55.3000))
assert p.ingest_source(con, "osm", [osm("node/4", "Other Spa", site="https://facebook.com/pearl",
                                        lat=25.3001, lon=55.3000)]) == (1, 0)

# similar name within 150 m merges; the same name 400 m away does not
load(google("g4", "Bright Smile Dental Center", lat=25.4000, lon=55.4000))
assert p.ingest_source(con, "osm", [osm("node/5", "Bright Smile Dental Centre", lat=25.4008, lon=55.4000)]) == (0, 1)
assert p.ingest_source(con, "osm", [osm("node/6", "Bright Smile Dental Center", lat=25.4036, lon=55.4000)]) == (1, 0)

# OSM first, then Google: the Google listing takes the lead over (its id, its values), no duplicate
before = count()
p.ingest_source(con, "osm", [osm("node/7", "Gulf Dental", phone="04 388 2222", site="https://gulf.ae",
                                 lat=25.5000, lon=55.5000)])
assert lead("osm:node/7") and count() == before + 1
new, dup = load(google("g5", "Gulf Dental Clinic", phone="+971 4 388 2222", lat=25.5002, lon=55.5000))
assert (new, dup) == (0, 1) and count() == before + 1 and lead("osm:node/7") is None
g5 = lead("g5")
assert g5["sources"] == "google_maps osm" and g5["name"] == "Gulf Dental Clinic" and g5["website"] == "https://gulf.ae"
assert con.execute("SELECT lead_id FROM lead_sources WHERE source_id = 'node/7'").fetchone()[0] == "g5"
# ...but two Google listings never merge with each other (Google's own ids decide that)
assert load(google("g6", "Gulf Dental Clinic", phone="+971 4 388 2222", lat=25.5002, lon=55.5000)) == (1, 0)

# a website filled in from another source gets read by the crawl
con.execute("UPDATE leads SET enriched_at = CURRENT_TIMESTAMP WHERE place_id = 'g4'")
p.ingest_source(con, "osm", [osm("node/8", "Bright Smile Dental Center", site="https://bright.ae",
                                 lat=25.4001, lon=55.4000)])
assert lead("g4")["website"] == "https://bright.ae" and lead("g4")["enriched_at"] is None

# the export shows every source and keeps the 20 columns
row = p.lead_row(g5)
assert len(row) == len(p.COLUMNS) and row[-1] == "google_maps osm"
row = p.lead_row(lead("osm:node/2"))
assert row[0] == "Smile Dental Marina" and row[8] == "dentist" and row[-1] == "osm" and "Google reviews" in row[17]

# old databases get the match keys filled in from the stored Google data
old = p.DATA / "old.db"
import sqlite3
c = sqlite3.connect(old)
c.execute("CREATE TABLE leads(place_id TEXT PRIMARY KEY, name TEXT, category TEXT, address TEXT, country TEXT, "
          "website TEXT, phone TEXT, phone_e164 TEXT, maps_link TEXT, rating REAL, reviews INTEGER, email TEXT, "
          "email_status TEXT, whatsapp INTEGER, query TEXT, scraped_at TEXT, raw TEXT)")
c.execute("INSERT INTO leads(place_id, name, website, phone, query, raw) VALUES (?,?,?,?,?,?)",
          ("old1", "Old", "https://old.ae", "04 566 2262", "AE|dentist|Dubai", json.dumps(google("old1", "Old"))))
c.commit()
c.close()
assert p.db(old).execute("SELECT phone_any, domain, lat FROM leads").fetchone() == ("+97145662262", "old.ae", 25.2)
print("all checks passed")
