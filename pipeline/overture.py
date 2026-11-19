"""Places from Overture Maps (CDLA Permissive 2.0), read with DuckDB straight from its public S3 bucket.

Free, no account, no token. Each country is copied once from the latest release into data/overture/<CC>.parquet
(the UAE: 30 s, 20 MB); every query after that is local. Sectors match whole ids in a place's taxonomy path from
categories.json ("dental_clinic" matches health_care > outpatient_care_facility > dental_clinic > general_dentistry).

Run alone: python pipeline/overture.py check CC   # ids in categories.json that match no place in that country
"""
import itertools
import sys

import httpx

import grid
import osm
import process as p

CATALOG = "https://stac.overturemaps.org/catalog.json"
BUCKET = "s3://overturemaps-us-west-2/release"
MIN_CONFIDENCE = 0.5  # Overture's own score that the place exists; below it are mostly stale or duplicate records
COLUMNS = ("id, names.primary AS name, confidence, phones, websites, emails, addresses, taxonomy.hierarchy AS hierarchy, "
           "taxonomy.primary AS category, (bbox.ymin + bbox.ymax) / 2 AS lat, (bbox.xmin + bbox.xmax) / 2 AS lon")
MATCH = "list_has_any(hierarchy, $kw)"


def keywords(sector):
    return osm.categories().get(sector, {}).get("overture", [])


def source(client):
    """The latest release's folder (Overture's public release catalog names it)."""
    r = client.get(CATALOG, timeout=30)
    r.raise_for_status()
    return f"{BUCKET}/{r.json()['latest']}"


def connect():
    import duckdb  # an optional dependency: a machine without it still runs every other source
    duck = duckdb.connect()
    duck.execute("INSTALL httpfs; LOAD httpfs; SET s3_region = 'us-west-2'; SET enable_progress_bar = false")
    return duck


def country_file(cc):
    return p.DATA / "overture" / f"{cc}.parquet"


def extract(duck, release, cc):
    """Copy one country's open places out of the release, once. The country's box (from Overture's own boundaries)
    lets DuckDB skip most of the world's files. shortcut: Stop waits for a first download to finish."""
    path = country_file(cc)
    if path.exists():
        return
    box = duck.execute(f"SELECT min(bbox.xmin), max(bbox.xmax), min(bbox.ymin), max(bbox.ymax) FROM read_parquet("
                       f"'{release}/theme=divisions/type=division_area/*') WHERE subtype = 'country' AND country = ?",
                       [cc]).fetchone()
    if box[0] is None:
        raise ValueError(f"no country {cc} in Overture's boundaries")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    duck.execute(f"COPY (SELECT {COLUMNS} FROM read_parquet('{release}/theme=places/type=place/*') "
                 f"WHERE bbox.xmin BETWEEN {box[0]} AND {box[1]} AND bbox.ymin BETWEEN {box[2]} AND {box[3]} "
                 f"AND addresses[1].country = '{cc}' AND coalesce(operating_status, 'open') = 'open') "
                 f"TO '{part.as_posix()}' (FORMAT parquet)")  # cc is one of phonenumbers' two-letter regions
    part.replace(path)


def places(duck, cc, sector, bbox):
    sql = f"SELECT * FROM read_parquet('{country_file(cc).as_posix()}') WHERE {MATCH} AND confidence >= $conf " \
          "AND (len(phones) > 0 OR len(list_filter(websites, lambda w: w <> '')) > 0 OR len(emails) > 0)"
    params = {"kw": keywords(sector), "conf": MIN_CONFIDENCE}
    if bbox:
        params |= dict(zip(("s", "w", "n", "e"), map(float, bbox.split(","))))
        sql += " AND lat BETWEEN $s AND $n AND lon BETWEEN $w AND $e"
    cur = duck.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def to_entry(r, sector, cc, location):
    """A gosom-shaped place for process.ingest_source."""
    first = lambda xs: next((x for x in xs or [] if x), "")
    addr = (r["addresses"] or [{}])[0]
    city = addr.get("locality") or ""
    return {"source_id": r["id"], "title": r["name"], "category": (r["category"] or sector).replace("_", " "),
            "address": addr.get("freeform") or city, "phone": first(r["phones"]), "web_site": first(r["websites"]),
            "emails": [e for e in r["emails"] or [] if e], "latitude": r["lat"], "longitude": r["lon"],
            "input_id": f"{cc}|{sector}|{location or city}",
            "link": f"https://www.google.com/maps/search/?api=1&query={r['lat']:.6f},{r['lon']:.6f}",
            "complete_address": {"city": city, "postal_code": addr.get("postcode") or "",
                                 "state": addr.get("region") or "", "country": cc}}


def pending(con):
    """(key, sector, cc, location, bbox) still to read: single cities first (their box), then whole countries."""
    sectors, locations = grid.targets()
    mapped = [s for s in sectors if keywords(s)]
    done = grid.done_keys(con)
    for loc, cc in locations:
        if loc != grid.ALL and (bbox := osm.location_box(con, loc, cc, geocode=True)):
            yield from ((f"overture:{s}|{loc}", s, cc, loc, bbox) for s in mapped if f"overture:{s}|{loc}" not in done)
    for loc, cc in locations:
        if loc == grid.ALL:
            yield from ((f"overture:{s}|*{cc}", s, cc, "", None) for s in mapped if f"overture:{s}|*{cc}" not in done)


def run_pending(con, client, limit, stop):
    stats = {"queries": 0, "new": 0, "merged": 0, "skipped": None}
    todo = list(itertools.islice(pending(con), limit))
    if not todo:
        return stats
    try:
        duck, release = connect(), source(client)
    except ImportError:
        return {**stats, "skipped": "duckdb not installed"}
    except Exception as e:  # the catalog is unreachable
        return {**stats, "skipped": f"Overture unreachable: {str(e)[:120]}"}
    for key, sector, cc, location, bbox in todo:
        if stop.is_set():
            break
        stats["queries"] += 1
        try:
            extract(duck, release, cc)
            entries = [to_entry(r, sector, cc, location) for r in places(duck, cc, sector, bbox)]
        except Exception as e:  # network, a damaged file: try again next cycle
            stats["skipped"] = f"Overture download failed: {str(e)[:120]}"
            break
        new, merged = p.ingest_source(con, "overture", entries)
        stats["new"] += new
        stats["merged"] += merged
        osm.mark(con, key)
    return stats


def unmatched(duck, cc, mapping):
    """{sector: ids that match no place in the country's file}: ids to correct in categories.json."""
    out = {}
    for sector, ids in mapping.items():
        missing = [k for k in ids if not duck.execute(
            f"SELECT 1 FROM read_parquet('{country_file(cc).as_posix()}') WHERE {MATCH} LIMIT 1", {"kw": [k]}).fetchone()]
        if missing:
            out[sector] = missing
    return out


if __name__ == "__main__":
    if sys.argv[1:2] != ["check"] or len(sys.argv) != 3:
        sys.exit(__doc__)
    cc = sys.argv[2].upper()
    duck = connect()
    with httpx.Client() as client:
        extract(duck, source(client), cc)
    for sector, ids in unmatched(duck, cc, {s: m.get("overture", []) for s, m in osm.categories().items()}).items():
        print(f"{sector}: no place has {', '.join(ids)}")
