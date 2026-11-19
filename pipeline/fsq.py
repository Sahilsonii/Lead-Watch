"""Places from Foursquare Open Source Places (Apache-2.0), read with DuckDB.

The dataset is free but gated on Hugging Face: accept its terms once at
https://huggingface.co/datasets/foursquare/fsq-os-places (no card), then put a read token in HF_TOKEN (never in a file).
Each country is copied once from the latest release into data/fsq/<CC>.parquet; every query after that is local.
Sectors match whole category-label segments from categories.json ("Dentist" matches "Health and Medicine > Dentist >
Pediatric Dentist"). Check the labels on real data with: python pipeline/fsq.py check AE

Run alone: python pipeline/fsq.py check CC   # keywords in categories.json that match no place in that country
"""
import itertools
import os
import sys

import httpx

import grid
import osm
import process as p

REPO = "foursquare/fsq-os-places"
COLUMNS = ("fsq_place_id, name, latitude, longitude, address, locality, region, postcode, country, tel, website, email, "
           "fsq_category_labels")
MATCH = "len(list_filter(fsq_category_labels, lambda l: list_has_any(string_split(lower(l), ' > '), $kw))) > 0"


def keywords(sector):
    return [k.lower() for k in osm.categories().get(sector, {}).get("fsq", [])]


def source(client):
    """The latest release's places files on Hugging Face (the folder listing is public; the files are not)."""
    r = client.get(f"https://huggingface.co/api/datasets/{REPO}/tree/main/release", timeout=30)
    r.raise_for_status()
    latest = max(d["path"] for d in r.json() if d["type"] == "directory")
    return f"hf://datasets/{REPO}/{latest}/places/parquet/*.parquet"


def connect(token):
    import duckdb  # only needed once HF_TOKEN is set; a machine without it still runs every other source
    duck = duckdb.connect()
    duck.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{token.replace(chr(39), '')}')")
    return duck


def country_file(cc):
    return p.DATA / "fsq" / f"{cc}.parquet"


def extract(duck, src, cc):
    """Copy one country's open places out of the release, once. shortcut: Stop waits for a first download to finish."""
    path = country_file(cc)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    duck.execute(f"COPY (SELECT {COLUMNS} FROM read_parquet('{src}') WHERE country = '{cc}' AND date_closed IS NULL) "
                 f"TO '{part.as_posix()}' (FORMAT parquet)")  # cc is one of phonenumbers' two-letter regions
    part.replace(path)


def places(duck, cc, sector, bbox):
    sql = f"SELECT {COLUMNS} FROM read_parquet('{country_file(cc).as_posix()}') WHERE {MATCH} " \
          "AND (tel IS NOT NULL OR website IS NOT NULL OR email IS NOT NULL)"
    params = {"kw": keywords(sector)}
    if bbox:
        params |= dict(zip(("s", "w", "n", "e"), map(float, bbox.split(","))))
        sql += " AND latitude BETWEEN $s AND $n AND longitude BETWEEN $w AND $e"
    cur = duck.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def num(v):
    return None if v is None else float(v)


def to_entry(r, sector, cc, location):
    """A gosom-shaped place for process.ingest_source."""
    kw = keywords(sector)
    label = next((l for l in r["fsq_category_labels"] or [] if set(l.lower().split(" > ")) & set(kw)), "")
    return {"source_id": r["fsq_place_id"], "title": r["name"], "category": label.split(" > ")[-1] or sector,
            "address": ", ".join(v for v in (r["address"], r["locality"]) if v), "phone": r["tel"] or "",
            "web_site": r["website"] or "", "emails": [r["email"]] if r["email"] else [],
            "latitude": num(r["latitude"]), "longitude": num(r["longitude"]),  # Decimal in the dataset
            "input_id": f"{cc}|{sector}|{location or r['locality'] or ''}",
            "link": f"https://foursquare.com/placemakers/review-place/{r['fsq_place_id']}",
            "complete_address": {"city": r["locality"] or "", "postal_code": r["postcode"] or "",
                                 "state": r["region"] or "", "country": cc}}


def pending(con):
    """(key, sector, cc, location, bbox) still to read: single cities first (their box), then whole countries."""
    sectors, locations = grid.targets()
    mapped = [s for s in sectors if keywords(s)]
    done = grid.done_keys(con)
    for loc, cc in locations:
        if loc != grid.ALL and (bbox := osm.location_box(con, loc, cc, geocode=True)):
            yield from ((f"fsq:{s}|{loc}", s, cc, loc, bbox) for s in mapped if f"fsq:{s}|{loc}" not in done)
    for loc, cc in locations:
        if loc == grid.ALL:
            yield from ((f"fsq:{s}|*{cc}", s, cc, "", None) for s in mapped if f"fsq:{s}|*{cc}" not in done)


def run_pending(con, client, limit, stop):
    stats = {"queries": 0, "new": 0, "merged": 0, "skipped": None}
    token = os.environ.get("HF_TOKEN")
    if not token:
        return {**stats, "skipped": "no HF_TOKEN"}
    todo = list(itertools.islice(pending(con), limit))
    if not todo:
        return stats
    try:
        duck, src = connect(token), source(client)
    except ImportError:
        return {**stats, "skipped": "duckdb not installed"}
    except Exception as e:  # Hugging Face unreachable, or no access granted yet
        return {**stats, "skipped": f"Foursquare unreachable: {str(e)[:120]}"}
    for key, sector, cc, location, bbox in todo:
        if stop.is_set():
            break
        stats["queries"] += 1
        try:
            extract(duck, src, cc)
            entries = [to_entry(r, sector, cc, location) for r in places(duck, cc, sector, bbox)]
        except Exception as e:  # gated (no access yet), network, a damaged file: try again next cycle
            stats["skipped"] = f"Foursquare download failed: {str(e)[:120]}"
            break
        new, merged = p.ingest_source(con, "foursquare", entries)
        stats["new"] += new
        stats["merged"] += merged
        osm.mark(con, key)
    return stats


def unmatched(duck, cc, mapping):
    """{sector: keywords that match no place in the country's file}: labels to correct in categories.json."""
    out = {}
    for sector, kws in mapping.items():
        missing = [k for k in kws if not duck.execute(
            f"SELECT 1 FROM read_parquet('{country_file(cc).as_posix()}') WHERE {MATCH} LIMIT 1",
            {"kw": [k.lower()]}).fetchone()]
        if missing:
            out[sector] = missing
    return out


if __name__ == "__main__":
    if sys.argv[1:2] != ["check"] or len(sys.argv) != 3:
        sys.exit(__doc__)
    cc = sys.argv[2].upper()
    duck = connect(os.environ["HF_TOKEN"])
    with httpx.Client() as client:
        extract(duck, source(client), cc)
    mapping = {s: m.get("fsq", []) for s, m in osm.categories().items()}
    for sector, kws in unmatched(duck, cc, mapping).items():
        print(f"{sector}: no place has {', '.join(kws)}")
