"""Places from OpenStreetMap through the Overpass API: free, no account, never IP-blocked for polite use.

One query per sector and location: a single city uses its cached geocode box; a whole country is one area query,
split into its GeoNames places when Overpass says it is too big. Only places with a phone, website or email are kept.
Data (c) OpenStreetMap contributors, ODbL: see data/ATTRIBUTION.txt next to leads.csv.
"""
import itertools
import json
import os
import re
import time

import httpx

import geo
import grid
import process as p

URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
TIMEOUT_S = 180
GAP_S = 2  # one query at a time, a pause between them (Overpass fair use)
MAX_AREA_KM2 = 50_000  # a single city's geocode bigger than this is a mistake, not a city
TAG_RE = re.compile(r"(\[[^=\[\]]+=[^\[\]]+\])+")  # one categories.json filter: [k=v][k2=v2]
PAIR_RE = re.compile(r"\[([^=\[\]]+)=([^\[\]]+)\]")
PHONE_TAGS = ("phone", "contact:phone", "mobile", "contact:mobile")
SITE_TAGS = ("website", "contact:website", "url")
EMAIL_TAGS = ("email", "contact:email")


def categories():
    return json.loads((p.ROOT / "categories.json").read_text(encoding="utf-8"))


def filters(sector):
    return categories().get(sector, {}).get("osm", [])


def overpass_query(fs, bbox, cc=None):
    """A union of the sector's tag filters over a "s,w,n,e" box, or over the whole country when bbox is None."""
    head, where = (f'area["ISO3166-1"="{cc}"][admin_level=2]->.a;', "(area.a)") if bbox is None else ("", f"({bbox})")
    return f"[out:json][timeout:{TIMEOUT_S}];{head}({''.join(f'nwr{f}{where};' for f in fs)});out center tags;"


def first(tags, keys):
    return next((tags[k].split(";")[0].strip() for k in keys if tags.get(k)), "")


def to_entry(el, sector, cc, location, fs):
    """A gosom-shaped place for process.ingest_source, or None without a name or any way to reach the business."""
    tags = el.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    phone, site = first(tags, PHONE_TAGS), first(tags, SITE_TAGS)
    emails = [m.strip() for k in EMAIL_TAGS for m in (tags.get(k) or "").split(";") if m.strip()]
    if not name or not (phone or site or emails):
        return None
    where = el.get("center") or el
    city = tags.get("addr:city:en") or tags.get("addr:city") or ""
    street = " ".join(v for v in (tags.get("addr:housenumber"), tags.get("addr:street")) if v)
    kind = next((tags[k] for f in fs for k, _ in PAIR_RE.findall(f)[:1] if tags.get(k)), sector)
    return {"source_id": f"{el['type']}/{el['id']}", "title": name, "category": kind.replace("_", " "),
            "address": ", ".join(v for v in (street, city) if v), "phone": phone, "web_site": site, "emails": emails,
            "latitude": where.get("lat"), "longitude": where.get("lon"), "input_id": f"{cc}|{sector}|{location or city}",
            "link": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "complete_address": {"city": city, "postal_code": tags.get("addr:postcode", ""),
                                 "state": tags.get("addr:state", ""), "country": cc}}


def location_box(con, name, cc, place=None, geocode=False):
    """A location's "s,w,n,e" box for the second sources: GeoNames' box for villages and towns, else the cached geocode."""
    if place and place.get("bbox"):
        return place["bbox"]
    if geocode:  # single cities are few: geocode them once into the planner's shared cache
        grid.location_bbox(con, name, cc)
    row = grid.cached_location(con, name, cc)  # country places wait for the Google planner's geocode
    return row[0] if row and row[0] and row[1] <= MAX_AREA_KM2 else None


def pending(con, load=geo.towns):
    """(key, sector, cc, location, bbox) of queries still to run, in the planner's order: single cities first, then
    whole countries (bbox None = the country area). Sectors without OSM tags are skipped."""
    sectors, locations = grid.targets()
    mapped = [s for s in sectors if filters(s)]
    done = grid.done_keys(con)
    box = lambda name, cc, place=None, geocode=False: location_box(con, name, cc, place, geocode)
    for loc, cc in locations:
        if loc != grid.ALL and (bbox := box(loc, cc, geocode=True)):
            yield from ((f"osm:{s}|{loc}", s, cc, loc, bbox) for s in mapped if f"osm:{s}|{loc}" not in done)
    for loc, cc in locations:
        if loc != grid.ALL:
            continue
        for s in mapped:
            if f"osm:{s}|*{cc}" in done:
                continue
            if f"osm-split:{s}|{cc}" not in done:
                yield f"osm:{s}|*{cc}", s, cc, "", None
                continue
            for t in load(cc):  # too big for one query: its places, smallest first
                if f"osm:{s}|{t['location']}" not in done and (bbox := box(t["location"], cc, t)):
                    yield f"osm:{s}|{t['location']}", s, cc, t["location"], bbox


def mark(con, key):
    con.execute("INSERT OR IGNORE INTO queries(q) VALUES (?)", (key,))
    con.commit()


def run_pending(con, client, limit, stop, load=geo.towns):
    """Up to `limit` Overpass queries. A busy or unreachable Overpass ends OSM for this cycle; the query stays queued."""
    stats = {"queries": 0, "new": 0, "merged": 0, "skipped": None}
    for key, sector, cc, location, bbox in itertools.islice(pending(con, load), limit):
        if stop.is_set():
            break
        if stats["queries"]:
            time.sleep(GAP_S)
        stats["queries"] += 1
        fs = filters(sector)
        try:
            r = client.post(URL, data={"data": overpass_query(fs, bbox, cc)}, timeout=TIMEOUT_S + 30,
                            headers={"User-Agent": grid.USER_AGENT})
        except httpx.HTTPError:
            stats["skipped"] = "Overpass unreachable"
            break
        try:
            data = r.json() if r.status_code == 200 else None
        except ValueError:  # an HTML "too busy" page with status 200
            data = None
        if data is None:
            stats["skipped"] = f"Overpass busy ({r.status_code})"
            break
        if "error" in (data.get("remark") or "").lower():  # timed out or out of memory: too big for one query
            mark(con, f"osm-split:{sector}|{cc}" if bbox is None else key)  # a box that fails is not retried forever
            continue
        entries = [e for el in data.get("elements", []) if (e := to_entry(el, sector, cc, location, fs))]
        new, merged = p.ingest_source(con, "osm", entries)
        stats["new"] += new
        stats["merged"] += merged
        mark(con, key)
    return stats
