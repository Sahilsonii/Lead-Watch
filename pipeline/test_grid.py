"""Self-check for grid run planning. Run: python pipeline/test_grid.py"""
import process as p
import grid

TARGETS = {"niches.txt": ["dentist", "real estate agency"], "cities.txt": ["Dubai,AE", "London,GB"]}
p.lines = lambda name: TARGETS[name]  # never read the real target files


def fake(boxes):
    calls = []

    def fetch(url):
        calls.append(url)
        box = boxes[len(calls) - 1]
        if isinstance(box, Exception):
            raise box
        return [{"boundingbox": box}] if box else []
    fetch.calls = calls
    return fetch


grid.time.sleep = lambda s: None  # skip the Nominatim politeness wait in tests

# Nominatim gives [south, north, west, east] as strings; gosom wants minLat,minLon,maxLat,maxLon
assert grid.geocode("Dubai", "AE", fake([["25.0", "25.4", "55.0", "55.6"]])) == (25.0, 55.0, 25.4, 55.6)
assert grid.geocode("Nowhere", "AE", fake([None])) is None

# a Nominatim outage is not remembered: the next plan geocodes again
con = p.db(":memory:")
assert grid.location_bbox(con, "Dubai", "AE", fake([OSError("down")])) is None
assert grid.cached_location(con, "Dubai", "AE") is None
assert 2680 < grid.bbox_area_km2((25.0, 55.0, 25.4, 55.6)) < 2700  # 44.5 km x 60.4 km

# a city-sized box -> one grid run over all its sectors
con = p.db(":memory:")
f = fake([["25.0", "25.3", "55.0", "55.3"], ["51.3", "51.7", "-0.6", "0.4"]])  # ~1,000 km2; ~3,085 km2
run = grid.next_run(con, f)
assert run["mode"] == "grid" and run["location"] == "Dubai" and run["cc"] == "AE"
assert run["bbox"] == "25.0,55.0,25.3,55.3" and run["cell_km"] == grid.CELL_KM
assert run["inputs"] == ["dentist #!#AE|dentist|Dubai", "real estate agency #!#AE|real estate agency|Dubai"]
assert run["keys"] == ["grid:dentist|Dubai", "grid:real estate agency|Dubai"]
assert grid.next_run(con, f)["location"] == "Dubai" and len(f.calls) == 1  # bbox cached

# finished keys move the planner to the next location; London's box is too big -> plain searches
con.executemany("INSERT INTO queries(q) VALUES (?)", [(k,) for k in run["keys"]])
run = grid.next_run(con, f)
assert run["location"] == "London" and run["mode"] == "plain" and run["bbox"] is None
assert run["inputs"][0] == "dentist in London #!#GB|dentist|London"
assert run["keys"] == ["dentist in London", "real estate agency in London"]
assert grid.progress(con) == (2, 4)
con.executemany("INSERT INTO queries(q) VALUES (?)", [(k,) for k in run["keys"]])
assert grid.next_run(con, f) is None and grid.progress(con) == (4, 4)

# a failed geocode also falls back to plain searches
con = p.db(":memory:")
run = grid.next_run(con, fake([None]))
assert run["mode"] == "plain" and run["location"] == "Dubai"
print("all checks passed")
