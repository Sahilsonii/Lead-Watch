"""Plan each scrape run as one location: a grid over its bounding box, or plain searches if no usable box.

Grid searches beat Google's ~120-results-per-search cap by searching many small map cells.
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request

import process as p

CELL_KM = float(os.environ.get("GRID_CELL_KM", 4))
MAX_GRID_AREA_KM2 = 2500  # bigger boxes are usually a whole emirate/province: too many cells
USER_AGENT = "LeadWatch/0.2 (business contact research; respects robots.txt)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def geocode(name, cc, fetch=http_get_json):
    """(min_lat, min_lon, max_lat, max_lon) from Nominatim, None when it finds nothing. Network errors raise."""
    q = urllib.parse.urlencode({"q": name, "countrycodes": cc.lower(), "format": "json", "limit": 1})
    hits = fetch(f"{NOMINATIM}?{q}")
    try:
        south, north, west, east = map(float, hits[0]["boundingbox"])  # Nominatim's order
    except (IndexError, KeyError, TypeError, ValueError):  # empty result or odd payload: no box
        return None
    return south, west, north, east


def bbox_area_km2(bbox):
    min_lat, min_lon, max_lat, max_lon = bbox
    height = (max_lat - min_lat) * 111.32
    width = (max_lon - min_lon) * 111.32 * math.cos(math.radians((min_lat + max_lat) / 2))
    return abs(height * width)


def cached_location(con, name, cc):
    return con.execute("SELECT bbox, area_km2 FROM locations WHERE name = ? AND cc = ?", (name, cc)).fetchone()


def location_bbox(con, name, cc, fetch=http_get_json):
    """gosom's "minLat,minLon,maxLat,maxLon" for a grid-sized location, else None. Geocodes once, ever."""
    row = cached_location(con, name, cc)
    if row is None:
        try:
            box = geocode(name, cc, fetch)
        except Exception:  # Nominatim down or rate-limiting: plain searches this time, geocode again next plan
            return None
        finally:
            time.sleep(1.1)  # Nominatim allows 1 request/s
        row = (",".join(map(str, box)), bbox_area_km2(box)) if box else (None, None)
        con.execute("INSERT INTO locations(name, cc, bbox, area_km2) VALUES (?,?,?,?)", (name, cc, *row))
        con.commit()
    bbox, area = row
    return bbox if bbox and area <= MAX_GRID_AREA_KM2 else None


def targets():
    sectors = list(dict.fromkeys(s.lower() for s in p.lines("niches.txt")))
    locations = [(c.strip(), cc.strip().upper()) for c, cc in (l.rsplit(",", 1) for l in p.lines("cities.txt"))]
    return sectors, list(dict.fromkeys(locations))


def done_keys(con):
    return {r[0].split(" #!#")[0] for r in con.execute("SELECT q FROM queries")}


def plain_key(sector, location):
    return f"{sector} in {location}"


def next_run(con, fetch=http_get_json):
    """The first location with sectors still to search, as one run; None when everything is done."""
    sectors, locations = targets()
    done = done_keys(con)
    for location, cc in locations:
        if all(f"grid:{s}|{location}" in done and plain_key(s, location) in done for s in sectors):
            continue  # finished either way: don't spend a geocode on it
        bbox = location_bbox(con, location, cc, fetch)
        if bbox:
            todo = [s for s in sectors if f"grid:{s}|{location}" not in done]
            inputs = [f"{s} #!#{cc}|{s}|{location}" for s in todo]
            keys = [f"grid:{s}|{location}" for s in todo]
        else:
            todo = [s for s in sectors if plain_key(s, location) not in done]
            inputs = [f"{plain_key(s, location)} #!#{cc}|{s}|{location}" for s in todo]
            keys = [plain_key(s, location) for s in todo]
        if todo:
            return {"location": location, "cc": cc, "mode": "grid" if bbox else "plain", "bbox": bbox,
                    "cell_km": CELL_KM if bbox else None, "inputs": inputs, "keys": keys}
    return None


def pair_done(con, sector, location, cc, done=None):
    """Done the way the planner sees it, using only the cached geocode (never calls Nominatim)."""
    done = done_keys(con) if done is None else done
    row = cached_location(con, location, cc)
    grid_mode = bool(row and row[0] and row[1] <= MAX_GRID_AREA_KM2)
    return (f"grid:{sector}|{location}" if grid_mode else plain_key(sector, location)) in done


def progress(con):
    """(done, total) sector x location pairs, for the dashboard."""
    sectors, locations = targets()
    done = done_keys(con)
    flags = [pair_done(con, s, loc, cc, done) for loc, cc in locations for s in sectors]
    return sum(flags), len(flags)
