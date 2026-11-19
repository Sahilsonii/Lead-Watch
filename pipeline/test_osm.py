"""Self-check for the OpenStreetMap source, against a fake Overpass. Run: python pipeline/test_osm.py"""
import json
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs

import httpx

import grid
import osm
import process as p

p.DATA = Path(tempfile.mkdtemp())
p._mx.update({"smile.ae": "mx.smile.ae"})
con = p.db()
osm.GAP_S = 0
osm.categories = lambda: {"dentist": {"osm": ["[amenity=dentist]", "[healthcare=dentist]"]},
                          "barber shop": {"osm": ["[shop=hairdresser][hairdresser=barber]"]}}

# queries: one per sector, a union of its tag filters, over a box or a whole country
q = osm.overpass_query(["[amenity=dentist]", "[healthcare=dentist]"], "25.0,55.0,25.3,55.3")
assert q == ("[out:json][timeout:180];(nwr[amenity=dentist](25.0,55.0,25.3,55.3);"
             "nwr[healthcare=dentist](25.0,55.0,25.3,55.3););out center tags;"), q
q = osm.overpass_query(["[shop=hairdresser][hairdresser=barber]"], None, "AE")
assert 'area["ISO3166-1"="AE"][admin_level=2]->.a;' in q and "nwr[shop=hairdresser][hairdresser=barber](area.a);" in q

# elements: English names and cities first, the first of several phones, ways use their center; no contact -> skipped
way = {"type": "way", "id": 7, "center": {"lat": 25.2, "lon": 55.24},
       "tags": {"name": "عيادة", "name:en": "Smile Clinic", "amenity": "dentist", "contact:phone": "+971 4 566 2262;+971 50 123 4567",
                "website": "https://smile.ae", "contact:email": "hi@smile.ae", "addr:street": "Jumeira Street",
                "addr:housenumber": "108", "addr:city": "دبي", "addr:city:en": "Dubai", "addr:postcode": "72789"}}
e = osm.to_entry(way, "dentist", "AE", "Dubai Marina", osm.filters("dentist"))
assert e["source_id"] == "way/7" and e["title"] == "Smile Clinic" and e["phone"] == "+971 4 566 2262"
assert (e["latitude"], e["longitude"]) == (25.2, 55.24) and e["emails"] == ["hi@smile.ae"] and e["category"] == "dentist"
assert e["address"] == "108 Jumeira Street, Dubai" and e["complete_address"]["city"] == "Dubai"
assert e["input_id"] == "AE|dentist|Dubai Marina" and e["link"] == "https://www.openstreetmap.org/way/7"
assert osm.to_entry({"type": "node", "id": 1, "lat": 1, "lon": 1, "tags": {"name": "Quiet", "amenity": "dentist"}},
                    "dentist", "AE", "Dubai", osm.filters("dentist")) is None
assert osm.to_entry({"type": "node", "id": 2, "lat": 1, "lon": 1, "tags": {"phone": "+971 4 566 2262"}},
                    "dentist", "AE", "Dubai", osm.filters("dentist")) is None  # no name

# --- running: single cities first (cached geocode box), then each whole country as one area query ---
grid.targets = lambda: (["dentist", "barber shop", "unmapped sector"], [("*", "AE"), ("Dubai Marina", "AE"), ("Nowhere", "AE")])
con.execute("INSERT INTO locations VALUES ('Dubai Marina', 'AE', '25.0,55.0,25.3,55.3', 900)")
con.execute("INSERT INTO locations VALUES ('Nowhere', 'AE', NULL, NULL)")  # geocode found nothing: no OSM either
con.commit()
assert [k for k, *_ in osm.pending(con)] == ["osm:dentist|Dubai Marina", "osm:barber shop|Dubai Marina",
                                             "osm:dentist|*AE", "osm:barber shop|*AE"]

asked, replies = [], []


def overpass(request):
    asked.append(parse_qs(request.content.decode())["data"][0])
    return replies.pop(0)


def client():
    return httpx.Client(transport=httpx.MockTransport(overpass))


ok = lambda *els: httpx.Response(200, json={"elements": list(els)})
replies += [ok(way), ok(), httpx.Response(429, text="<html>rate limited</html>")]
stats = osm.run_pending(con, client(), 10, threading.Event())
assert stats == {"queries": 3, "new": 1, "merged": 0, "skipped": "Overpass busy (429)"}, stats
assert "(25.0,55.0,25.3,55.3)" in asked[0] and "(area.a)" in asked[2]
assert [k for k, *_ in osm.pending(con)] == ["osm:dentist|*AE", "osm:barber shop|*AE"]  # busy: left for next cycle
lead = con.execute("SELECT place_id, sources, email, phone_e164 FROM leads").fetchone()
assert lead == ("osm:way/7", "osm", "hi@smile.ae", None), lead

# the same element found again by the country query merges into the same lead
replies += [ok(way), ok()]
assert osm.run_pending(con, client(), 10, threading.Event())["new"] == 0
assert con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1 and not list(osm.pending(con))

# a country too big for one query is split into its places (GeoNames boxes, or cached geocodes)
grid.targets = lambda: (["dentist"], [("*", "IN")])
towns = [{"location": "Rampur", "bbox": "26.0,80.0,26.1,80.1"}, {"location": "Pune", "bbox": None},
         {"location": "Maharashtra", "bbox": None}]
con.execute("INSERT INTO locations VALUES ('Pune', 'IN', '18.4,73.7,18.6,73.9', 400)")
con.commit()
replies += [httpx.Response(200, json={"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 1"})]
assert osm.run_pending(con, client(), 1, threading.Event(), load=lambda cc: towns)["queries"] == 1
assert [k for k, *_ in osm.pending(con, load=lambda cc: towns)] == ["osm:dentist|Rampur", "osm:dentist|Pune"]

# Stop is honored between queries
stop = threading.Event()
stop.set()
assert osm.run_pending(con, client(), 10, stop, load=lambda cc: towns)["queries"] == 0

# no network: the cycle goes on without OSM
def down(request):
    raise httpx.ConnectError("offline")
stats = osm.run_pending(con, httpx.Client(transport=httpx.MockTransport(down)), 10, threading.Event(), load=lambda cc: towns)
assert stats["skipped"] == "Overpass unreachable" and stats["queries"] == 1

# the shipped categories.json maps every sector in sectors.txt, with well-formed filters
real = json.loads((p.ROOT / "categories.json").read_text(encoding="utf-8"))
assert set(real) == set(p.lines("sectors.txt"))
assert all(m["osm"] and all(osm.TAG_RE.fullmatch(f) for f in m["osm"]) for m in real.values())
print("all checks passed")
