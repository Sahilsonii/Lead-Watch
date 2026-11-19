"""Self-check for the Overture Maps source, against a small local copy of a release. Run: python pipeline/test_overture.py"""
import tempfile
import threading
from pathlib import Path

import duckdb

import grid
import overture as ov
import process as p

p.DATA = Path(tempfile.mkdtemp())
p._mx.update({"smile.ae": "mx.smile.ae"})
con = p.db()
ov.osm.categories = lambda: {"dentist": {"overture": ["dental_clinic"]}, "bar": {"overture": ["bar"]},
                             "gym": {"osm": ["[leisure=fitness_centre]"]}}

# a fake release: Overture's folder layout, its column shapes, two countries' boundaries
release = p.DATA / "release"
for sub in ("theme=divisions/type=division_area", "theme=places/type=place"):
    (release / sub).mkdir(parents=True)
duck = duckdb.connect()
duck.execute(f"""COPY (SELECT * FROM (VALUES ('country', 'AE', {{'xmin': 51.4, 'xmax': 56.6, 'ymin': 22.6, 'ymax': 26.2}}),
    ('country', 'GB', {{'xmin': -8.6, 'xmax': 1.8, 'ymin': 49.9, 'ymax': 60.9}}),
    ('region', 'AE', {{'xmin': 0.0, 'xmax': 90.0, 'ymin': 0.0, 'ymax': 90.0}})) t(subtype, country, bbox))
    TO '{(release / "theme=divisions/type=division_area/0.parquet").as_posix()}' (FORMAT parquet)""")


def place(id, name, lat, lon, hierarchy, phones="NULL", websites="NULL", emails="NULL", cc="AE", conf=0.9, status="NULL",
          city="Dubai"):
    h = "[" + ", ".join(f"'{x}'" for x in hierarchy) + "]"
    return (f"('{id}', {{'primary': '{name}'}}, {conf}, {phones}, {websites}, {emails}, "
            f"[{{'freeform': '1 Beach Rd', 'locality': '{city}', 'postcode': NULL, 'region': NULL, 'country': '{cc}'}}], "
            f"{{'primary': '{hierarchy[-1]}', 'hierarchy': {h}}}, {{'xmin': {lon}, 'xmax': {lon}, 'ymin': {lat}, 'ymax': {lat}}}, "
            f"{status})")


DENTAL = ["health_care", "outpatient_care_facility", "dental_clinic"]
rows = [
    place("o1", "Smile Clinic", 25.2, 55.24, DENTAL, phones="['+97145662262']", websites="['https://smile.ae']",
          emails="['hi@smile.ae']"),
    place("o2", "Kids Teeth", 25.21, 55.25, DENTAL + ["pediatric_dentistry"], websites="['', 'https://kids.ae']"),
    place("o3", "Barbers Inc", 25.2, 55.24, ["lifestyle_services", "personal_or_beauty_service", "barber"],
          phones="['+97143882222']"),
    place("o4", "Quiet Dentist", 25.2, 55.24, DENTAL, websites="['']"),  # no contact at all
    place("o5", "Gone Dental", 25.2, 55.24, DENTAL, phones="['+97143456789']", status="'permanently_closed'"),
    place("o6", "Ghost Dental", 25.2, 55.24, DENTAL, phones="['+97143456780']", conf=0.2),  # Overture doubts it
    place("o7", "Far Dental", 24.4, 54.4, DENTAL, phones="['+97123456789']", city="Abu Dhabi"),
    place("o8", "London Dental", 51.5, -0.1, DENTAL, phones="['+442079460958']", cc="GB", city="London"),
    place("o9", "Muscat Dental", 23.6, 56.5, DENTAL, phones="['+96824123456']", cc="OM", city="Muscat"),  # inside AE's box
]
duck.execute(f"""COPY (SELECT * FROM (VALUES {", ".join(rows)}) t(id, names, confidence, phones, websites, emails,
    addresses, taxonomy, bbox, operating_status)) TO '{(release / "theme=places/type=place/0.parquet").as_posix()}' (FORMAT parquet)""")
ov.source = lambda client: release.as_posix()
ov.connect = duckdb.connect

# a country is copied once, open places in it only; sectors match whole ids ("bar" never matches "barber")
grid.targets = lambda: (["dentist", "bar", "gym"], [("Dubai Marina", "AE"), ("*", "AE")])
con.execute("INSERT INTO locations VALUES ('Dubai Marina', 'AE', '25.1,55.1,25.3,55.3', 900)")
con.commit()
assert [k for k, *_ in ov.pending(con)] == ["overture:dentist|Dubai Marina", "overture:bar|Dubai Marina",
                                            "overture:dentist|*AE", "overture:bar|*AE"]
stats = ov.run_pending(con, None, 10, threading.Event())
assert stats == {"queries": 4, "new": 3, "merged": 0, "skipped": None}, stats  # o1, o2 in the box; o7 country-wide
assert ov.country_file("AE").exists() and not ov.country_file("GB").exists()
assert duck.execute(f"SELECT count(*) FROM '{ov.country_file('AE').as_posix()}'").fetchone()[0] == 6  # not o5, o8, o9
leads = dict(con.execute("SELECT name, sources FROM leads").fetchall())
assert set(leads) == {"Smile Clinic", "Kids Teeth", "Far Dental"} and set(leads.values()) == {"overture"}, leads
smile = con.execute("SELECT category, email, query, phone_any, maps_link FROM leads WHERE name = 'Smile Clinic'").fetchone()
assert smile[:4] == ("dental clinic", "hi@smile.ae", "AE|dentist|Dubai Marina", "+97145662262"), smile
assert smile[4].endswith("query=25.200000,55.240000"), smile
assert con.execute("SELECT website, category FROM leads WHERE name = 'Kids Teeth'").fetchone() == \
    ("https://kids.ae", "pediatric dentistry")  # the first non-empty website, the most specific category
assert con.execute("SELECT query FROM leads WHERE name = 'Far Dental'").fetchone()[0] == "AE|dentist|Abu Dhabi"
assert not list(ov.pending(con))

# ids with no place behind them are reported, so categories.json can be corrected on real data
assert ov.unmatched(duck, "AE", {"dentist": ["dental_clinic", "tooth_doctor"], "bar": ["bar"]}) == \
    {"dentist": ["tooth_doctor"], "bar": ["bar"]}

# a country Overture has no boundary for, or a failed download, ends Overture for the cycle; nothing is marked done
grid.targets = lambda: (["dentist"], [("*", "ZZ")])
stats = ov.run_pending(con, None, 10, threading.Event())
assert stats["skipped"].startswith("Overture download failed") and list(ov.pending(con)), stats
print("all checks passed")
