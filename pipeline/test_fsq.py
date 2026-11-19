"""Self-check for the Foursquare Open Places source, against a small local parquet file. Run: python pipeline/test_fsq.py"""
import os
import tempfile
import threading
from pathlib import Path

import duckdb

import fsq
import grid
import process as p

p.DATA = Path(tempfile.mkdtemp())
p._mx.update({"smile.ae": "mx.smile.ae"})
con = p.db()
fsq.osm.categories = lambda: {"dentist": {"fsq": ["Dentist"]}, "bar": {"fsq": ["Bar"]}, "gym": {"osm": ["[leisure=fitness_centre]"]}}

# a fake release: the documented columns, three countries' worth of rows
release = p.DATA / "release.parquet"
duckdb.connect().execute(f"""COPY (SELECT * FROM (VALUES
    ('f1', 'Smile Clinic', 25.2, 55.24, '108 Jumeira St', 'Dubai', 'Dubai', '72789', 'AE', '04 566 2262', 'https://smile.ae', 'hi@smile.ae', ['Health and Medicine > Dentist'], NULL),
    ('f2', 'Kids Teeth', 25.21, 55.25, NULL, 'Dubai', 'Dubai', NULL, 'AE', NULL, 'https://kids.ae', NULL, ['Health and Medicine > Dentist > Pediatric Dentist'], NULL),
    ('f3', 'Fade Barbershop', 25.2, 55.24, NULL, 'Dubai', 'Dubai', NULL, 'AE', '04 388 2222', NULL, NULL, ['Retail > Health and Beauty Service > Barbershop'], NULL),
    ('f4', 'Quiet Dentist', 25.2, 55.24, NULL, 'Dubai', 'Dubai', NULL, 'AE', NULL, NULL, NULL, ['Health and Medicine > Dentist'], NULL),
    ('f5', 'Gone Dental', 25.2, 55.24, NULL, 'Dubai', 'Dubai', NULL, 'AE', '04 345 6789', NULL, NULL, ['Health and Medicine > Dentist'], DATE '2024-01-01'),
    ('f6', 'Far Dental', 24.4, 54.4, NULL, 'Abu Dhabi', 'Abu Dhabi', NULL, 'AE', '02 345 6789', NULL, NULL, ['Health and Medicine > Dentist'], NULL),
    ('f7', 'London Dental', 51.5, -0.1, NULL, 'London', 'England', NULL, 'GB', '020 7946 0958', NULL, NULL, ['Health and Medicine > Dentist'], NULL)
) t(fsq_place_id, name, latitude, longitude, address, locality, region, postcode, country, tel, website, email,
    fsq_category_labels, date_closed)) TO '{release.as_posix()}' (FORMAT parquet)""")
fsq.source = lambda client: release.as_posix()
fsq.connect = lambda token: duckdb.connect()

# skipped without a token: the dataset needs one free "I agree" on Hugging Face first
os.environ.pop("HF_TOKEN", None)
assert fsq.run_pending(con, None, 10, threading.Event())["skipped"] == "no HF_TOKEN"
os.environ["HF_TOKEN"] = "test-token"

# a country is copied once, open places only; sectors match whole label segments ("Bar" never matches "Barbershop")
grid.targets = lambda: (["dentist", "bar", "gym"], [("Dubai Marina", "AE"), ("*", "AE")])
con.execute("INSERT INTO locations VALUES ('Dubai Marina', 'AE', '25.1,55.1,25.3,55.3', 900)")
con.commit()
assert [k for k, *_ in fsq.pending(con)] == ["fsq:dentist|Dubai Marina", "fsq:bar|Dubai Marina", "fsq:dentist|*AE", "fsq:bar|*AE"]
stats = fsq.run_pending(con, None, 10, threading.Event())
assert stats == {"queries": 4, "new": 3, "merged": 0, "skipped": None}, stats  # f1, f2 in the box; f6 country-wide
assert (p.DATA / "fsq" / "AE.parquet").exists() and not (p.DATA / "fsq" / "GB.parquet").exists()
rows = dict(con.execute("SELECT name, sources FROM leads").fetchall())
assert set(rows) == {"Smile Clinic", "Kids Teeth", "Far Dental"} and set(rows.values()) == {"foursquare"}, rows
smile = con.execute("SELECT category, email, query, phone_any FROM leads WHERE name = 'Smile Clinic'").fetchone()
assert smile == ("Dentist", "hi@smile.ae", "AE|dentist|Dubai Marina", "+97145662262"), smile
assert con.execute("SELECT query FROM leads WHERE name = 'Far Dental'").fetchone()[0] == "AE|dentist|Abu Dhabi"
assert not list(fsq.pending(con))

# keywords with no place behind them are reported, so the guessed labels can be corrected on real data
assert fsq.unmatched(duckdb.connect(), "AE", {"dentist": ["Dentist", "Teeth Doctor"], "bar": ["Bar"]}) == \
    {"dentist": ["Teeth Doctor"], "bar": ["Bar"]}

# a failed download ends Foursquare for the cycle; nothing is marked done
grid.targets = lambda: (["dentist"], [("*", "GB")])
fsq.source = lambda client: (p.DATA / "missing" / "*.parquet").as_posix()
stats = fsq.run_pending(con, None, 10, threading.Event())
assert stats["skipped"].startswith("Foursquare download failed") and list(fsq.pending(con)), stats
print("all checks passed")
