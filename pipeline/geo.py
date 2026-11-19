"""Every place in a country, for "All of <country>" targets. Data: GeoNames (CC BY 4.0).

Order, smallest areas first: villages (1,000-15,000 people), towns (15,000-100,000), cities (100,000+), each state,
then the whole country; the least populated first inside each level. Downloaded once into data/geo/.
"""
import io
import math
import urllib.request
import zipfile
from collections import Counter, defaultdict
from functools import lru_cache

import process as p

BASE = "https://download.geonames.org/export/dump/"
SOURCES = {"cities1000.txt": "cities1000.zip", "admin1CodesASCII.txt": "admin1CodesASCII.txt",
           "countryInfo.txt": "countryInfo.txt"}
SKIP = {"PPLX", "PPLH", "PPLQ", "PPLW"}  # neighborhoods; historical, abandoned and destroyed places
LEVELS = ["village", "town", "city", "state", "country"]
BOX_KM = {"village": 4, "town": 8}  # square around GeoNames' point; cities and up are geocoded for their real extent


def fetch(name):
    """Path to a GeoNames file, downloaded on first use."""
    path = p.DATA / "geo" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(BASE + SOURCES[name], timeout=300) as r:
            body = r.read()
        if SOURCES[name].endswith(".zip"):
            body = zipfile.ZipFile(io.BytesIO(body)).read(name)
        part = path.with_suffix(".part")
        part.write_bytes(body)
        part.replace(path)  # never leave half a file behind for the next start to trust
    return path


def box(lat, lon, km):
    """gosom's "minLat,minLon,maxLat,maxLon" for a km x km square centered on the point."""
    dlat = km / 2 / 111.32
    dlon = km / 2 / (111.32 * math.cos(math.radians(lat)))
    return ",".join(f"{v:.5f}" for v in (lat - dlat, lon - dlon, lat + dlat, lon + dlon))


def level_of(pop):
    return "village" if pop < 15_000 else "town" if pop < 100_000 else "city"


def parse(cities_text, admin1_text, countries_text, cc):
    """Ordered places of one country: location (unique name), query (for Nominatim), bbox, level, n of, state."""
    states = dict((line.split("\t") + [""])[:2] for line in admin1_text.splitlines())
    country = next((f[4] for f in (l.split("\t") for l in countries_text.splitlines() if not l.startswith("#"))
                    if f[0] == cc and len(f) > 4), cc)
    rows, state_pop = [], defaultdict(int)
    for line in cities_text.splitlines():
        f = line.split("\t")
        if len(f) > 14 and f[8] == cc and f[7] not in SKIP:
            # the ASCII name ("Hatta", not "Ḩattā") is what people type; "#" and "|" delimit gosom's input id
            name = " ".join((f[2] or f[1]).replace("#", " ").replace("|", " ").split())
            pop, state = int(f[14] or 0), states.get(f"{cc}.{f[10]}", "")
            rows.append({"name": name, "pop": pop, "state": state, "lat": float(f[4]), "lon": float(f[5])})
            state_pop[state] += pop
    if not rows:
        return []

    # names: own name; "Name, State" if it repeats in the country; then "Name, State 2", 3... if it still does
    rows.sort(key=lambda r: (r["pop"], r["name"]))
    by_name = Counter(r["name"] for r in rows)
    taken = Counter()
    by_level = defaultdict(list)
    for r in rows:
        query = f"{r['name']}, {r['state']}" if r["state"] else r["name"]
        label = r["name"] if by_name[r["name"]] == 1 else query
        taken[label] += 1
        level = level_of(r["pop"])
        by_level[level].append({"location": label if taken[label] == 1 else f"{label} {taken[label]}", "query": query,
                                "bbox": box(r["lat"], r["lon"], BOX_KM[level]) if level in BOX_KM else None,
                                "level": level, "state": r["state"]})
    # a state named like a place ("Dubai", "Florida") becomes "Dubai, United Arab Emirates", so they never share a search
    places = {t["location"] for level in ("village", "town", "city") for t in by_level[level]}
    named = lambda s: f"{s}, {country}" if s in places else s
    by_level["state"] = [{"location": named(s), "query": named(s), "bbox": None, "level": "state", "state": s}
                         for s, _ in sorted(state_pop.items(), key=lambda kv: (kv[1], kv[0])) if s]
    # a country named like one of its places or states (Singapore, Guatemala) is already covered by it: no extra search
    covered = places | {t["location"] for t in by_level["state"]}
    by_level["country"] = [{"location": country, "query": country, "bbox": None, "level": "country", "state": ""}
                           ] if country not in covered else []

    out = []
    for level in LEVELS:
        for n, t in enumerate(by_level[level], 1):
            out.append({**t, "n": n, "of": len(by_level[level])})
    return out


@lru_cache(maxsize=None)
def towns(cc):
    read = lambda name: fetch(name).read_text(encoding="utf-8")
    return parse(read("cities1000.txt"), read("admin1CodesASCII.txt"), read("countryInfo.txt"), cc)


def summary(places):
    """How many places per level, e.g. {"village": 4300, "town": 800, ...}."""
    return dict(Counter(t["level"] for t in places))
