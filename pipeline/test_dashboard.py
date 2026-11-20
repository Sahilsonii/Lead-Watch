"""Self-check for the scraper command the runner builds. Run: python pipeline/test_dashboard.py"""
import dashboard as d

grid_run = {"mode": "grid", "bbox": "25.0,55.0,25.3,55.3", "cell_km": 4.0,
            "inputs": ["dentist #!#AE|dentist|Dubai"], "keys": ["grid:dentist|Dubai"]}
plain_run = {"mode": "plain", "bbox": None, "cell_km": None,
             "inputs": ["dentist in London #!#GB|dentist|London"], "keys": ["dentist in London"]}

args = d.docker_args(grid_run, "raw-1.json", resume=False)
for flag in ["-grid-bbox", "25.0,55.0,25.3,55.3", "-grid-cell", "4.0", "-zoom", "-depth", "10"]:
    assert flag in args, flag
assert "-grid-strict" not in args  # places just outside a village box are kept; ingest keeps the country instead
assert args[args.index("-depth") + 1] == "10" and "-resume" not in args
assert "/out/raw-1.json" in args and "/out/batch.txt" in args

# gosom resumes only with identical options: -resume is the one difference
resumed = d.docker_args(grid_run, "raw-1.json", resume=True)
assert "-resume" in resumed and [a for a in resumed if a != "-resume"] == args

# a resume uses the run's own depth and concurrency, even if the env changed since it started
kept = d.docker_args({**grid_run, "depth": 3, "concurrency": 1}, "raw-1.json", resume=True)
assert kept[kept.index("-depth") + 1] == "3" and kept[kept.index("-c") + 1] == "1"

plain = d.docker_args(plain_run, "raw-2.json", resume=False)
assert not any(a.startswith("-grid") for a in plain) and "-zoom" not in plain

# --- runner: an unfinished run is resumed as-is; a crashing run is dropped after MAX_RESUMES ---
import tempfile
from pathlib import Path
import grid

tmp = Path(tempfile.mkdtemp())
d.STATE_FILE = tmp / "state.json"
d.p.DATA = tmp
d.p.ingest = lambda path, keys: ingested.append(list(keys)) or (0, 0)
d.p.export = lambda *a: 0
real_db, fake_db = d.p.db, lambda path=None: type("C", (), {"execute": lambda *a: []})()
d.p.db = fake_db
ingested, launched = [], []


class Proc:
    def __init__(self, code):
        self.returncode = code


def fake_run(code, places=1):
    def run(args, **kw):
        if args[:2] == ["docker", "info"]:  # Docker is up
            return Proc(0)
        launched.append(args)
        (tmp / "raw-1.json").write_text('{"title": "x"}\n' * places)
        return Proc(code)
    return run


grid.targets = lambda: (["dentist"], [("Dubai", "AE")])
saved = {**grid_run, "location": "Dubai", "cc": "AE", "results": "raw-1.json", "resumes": 0}
d.save_state(run=saved)
grid.next_run = lambda con: (_ for _ in ()).throw(AssertionError("must resume, not plan a new run"))
r = d.Runner()
d.subprocess.run = fake_run(0)
r.scrape_once = (lambda orig: lambda run, resume: (orig(run, resume), r.stop.set()))(r.scrape_once)
r.loop()
assert "-resume" in launched[-1] and "/out/raw-1.json" in launched[-1]
assert ingested[-1] == ["grid:dentist|Dubai"] and d.load_state()["run"] is None  # finished -> keys done

d.save_state(run=saved)
r = d.Runner()
d.subprocess.run = fake_run(1)  # crash
for i in range(d.MAX_RESUMES + 1):
    r.scrape_once(d.load_state()["run"], resume=i > 0)
    assert ingested[-1] == []  # nothing marked done on a crash
assert d.load_state()["run"] is None  # dropped after the third crash; its searches stay queued

d.save_state(run=saved, blocks={"google": {"score": 2, "pause_min": 120, "stopped": False, "kind": "blocked"}})
before = d.load_state()["blocks"]
r = d.Runner()
r.stop.set()  # Stop pressed: docker exits non-zero, run is kept unchanged for the next Start
r.scrape_once(saved, resume=False)
assert d.load_state()["run"] == saved
assert d.load_state()["blocks"] == before  # a manual Stop never moves the block score
d.save_state(blocks={})

# blocked (clean exit, zero places): nothing marked done, run kept for resume, pause doubled
r = d.Runner()
d.subprocess.run = fake_run(0, places=0)
r.scrape_once(saved, resume=False)
st = d.load_state()
assert ingested[-1] == [] and st["run"] == saved
assert st["blocks"]["google"] == {"score": 1, "pause_min": 2 * d.PAUSE_MIN, "stopped": False, "kind": "blocked"}
assert st["next_attempt"]

# fourth block in a row: the loop stops itself and says why
d.save_state(blocks={"google": {"score": 3, "pause_min": 240, "stopped": False, "kind": "blocked"}}, running=True)
r = d.Runner()
r.scrape_once(saved, resume=False)
assert r.stop.is_set() and r.phase == d.backoff.STOP_MESSAGE and d.load_state()["running"] is False

# cycle order: scrape -> enrich websites -> LinkedIn lookups -> export, then rest
order = []


async def fake_enrich(con, limit, stop):
    order.append("enrich")
    return {"crawled": 0, "names": 0, "blocked": 0}
d.enrich.enrich_pending = fake_enrich
d.linkedin.lookup_pending = lambda con, client, limit, stop: order.append("linkedin") or {}
d.osm.run_pending = lambda con, client, limit, stop: order.append("osm") or {"new": 0, "merged": 0}
d.fsq.run_pending = lambda con, client, limit, stop: order.append("fsq") or {"new": 0, "merged": 0, "skipped": "no HF_TOKEN"}
d.overture.run_pending = lambda con, client, limit, stop: order.append("overture") or {"new": 0, "merged": 0}
r = d.Runner()
d.p.export = lambda *a: order.append("export") or r.stop.set() or 0
r.scrape_once = lambda run, resume: order.append("scrape")
d.save_state(run=saved, running=True)
r.loop()
assert order == ["scrape", "osm", "fsq", "overture", "enrich", "linkedin", "export"], order  # new places get their sites read
assert r.sources["foursquare"]["skipped"] == "no HF_TOKEN"

# Google's 30-minute rest is spent reading websites (other hosts), then one export
batches, exported = [5, 3, 0], []


async def batch_enrich(con, limit, stop):
    return {"crawled": batches.pop(0) if batches else 0}
d.enrich.enrich_pending = batch_enrich
d.p.export = lambda *a: exported.append(a) or 0
r = d.Runner()
r.rest(0.3)
assert batches == [] and len(exported) == 1, (batches, exported)
r.stop.set()
batches[:] = [9]
r.rest(5)
assert batches == [9]  # Stop pressed: no reading during the rest
d.enrich.enrich_pending = fake_enrich

# "Stop after N new leads": once N wait for the sheet, export exactly N under this Start's date-time and stop
exports, waiting = [], ["lead"] * 7
d.p.not_in_sheet = lambda con: waiting
d.p.export = lambda batch=None, limit=None: exports.append((batch, limit)) or min(limit or 99, len(waiting))
order = []
r = d.Runner()
r.scrape_once = lambda run, resume: order.append("scrape")
d.save_state(run=saved, running=True)
r.start(limit=5)
r.thread.join(5)
assert order == ["scrape"] and exports == [(r.batch, 5)], (order, exports)  # no other source, no website reading
assert r.written == 5 and r.phase == "Stopped after 5 new leads" and d.load_state()["running"] is False
assert d.time.strptime(r.batch, "%Y-%m-%d %H:%M")
waiting = ["lead"] * 2
r = d.Runner()
r.limit, r.batch = 5, "2026-11-20 04:05"
assert not r.at_limit(None) and not r.stop.is_set()  # 2 of 5: keep going
r.limit = None
assert not r.at_limit(None)  # no limit: the loop runs as before
waiting, d.p.export = ["lead"] * 7, lambda *a: 0  # leads.xlsx open in Excel: nothing written
r = d.Runner()
r.limit, r.batch = 5, "2026-11-20 04:05"
assert r.at_limit(None) and r.stop.is_set() and "open in Excel" in r.phase, r.phase

# run panel: grid cells = rows x columns of the bbox, per sector; done = finished Maps search jobs in the log
log = '{"message":"job finished","job":"Job{URL: https://www.google.com/maps/search/a}","status":"success"}\n' * 3
info = d.run_info({**grid_run, "location": "Dubai", "inputs": ["a", "b"]}, log)
assert info["cells_total"] == 2 * d.math.ceil(0.3 * 111.32 / 4) * d.math.ceil(0.3 * 111.32 * d.math.cos(d.math.radians(25.15)) / 4)
assert info["cells_done"] == 3 and info["mode"] == "grid" and 900 < info["area_km2"] < 1100, info
plain_info = d.run_info({"location": "London", "mode": "plain", "bbox": None, "cell_km": None, "inputs": ["a"]}, "")
assert plain_info["cells_total"] is None and plain_info["area_km2"] is None
assert d.run_info(None, "") is None

# live run: gosom -resume writes raw-X.json.resume.json next to the results; it is not a results file
for name in ("raw-1.json", "raw-2.json", "raw-2.json.resume.json"):
    (tmp / name).write_text("{}\n")
assert [f.name for f in d.raw_files()] == ["raw-1.json", "raw-2.json"]

# the page's status comes from a copy kept fresh in the background; a failed refresh keeps the last good one
real_stats = d.stats
d.stats = lambda: {"leads": 7}
d.refresh_status()
assert d.LATEST["body"] == b'{"leads": 7}'
d.stats = lambda: 1 / 0
d.refresh_status()
assert d.LATEST["body"] == b'{"leads": 7}' and "ZeroDivisionError" in (tmp / "runner.log").read_text()
d.stats = real_stats

# Clear: the feed hides the places already in it; later places show, and the next run's file shows in full
import json
d.p.db = lambda path=None: real_db(tmp / "leads.db")
con = d.p.db()
add = lambda pid, src, title: (con.execute("INSERT INTO leads(place_id, name, sources, raw) VALUES (?,?,?,?)",
                                           (pid, title, src, json.dumps({"title": title, "link": "x/" + pid}))), con.commit())
add("osm:1", "osm", "Old OSM place")
(tmp / "raw-2.json").write_text('{"title": "a"}\n{"title": "b"}\n')
d.clear_feed()
assert d.since_clear(d.live_rows(tmp / "raw-2.json"), "raw-2.json") == [] and d.other_source_leads(con) == []
(tmp / "raw-2.json").write_text('{"title": "a"}\n{"title": "b"}\n{"title": "c"}\n')
assert d.since_clear(d.live_rows(tmp / "raw-2.json"), "raw-2.json") == [{"title": "c"}]
assert len(d.since_clear([{}, {}], "raw-3.json")) == 2

# other sources join the feed: new OpenStreetMap/Foursquare/Overture leads show; Google-first leads come from the raw file
add("osm:2", "osm", "New OSM place")
add("ChIJ1", "google_maps osm", "Google place")
add("foursquare:3", "foursquare overture", "New FSQ place")
assert [x[1] for x in d.other_source_leads(con)] == ["foursquare:3", "osm:2"]
feed = d.stats()["feed"]
assert {(r["name"], r["source"]) for r in feed} == {("c", "Google Maps"), ("New OSM place", "OpenStreetMap"),
                                                    ("New FSQ place", "Foursquare")}, feed
# Removed card: leads kept out of the files, by reason and by the source that found them first
add("fsq:9", "foursquare overture", "Gone site")
con.execute("UPDATE leads SET phone = '+44 20 7946 0000', site_status = 'gone' WHERE place_id = 'fsq:9'")
con.commit()
s = d.stats()
assert s["removed"] == {"total": 5, "reasons": {"no phone": 4, "website gone": 1},
                        "sources": {"osm": 2, "foursquare": 2, "google_maps": 1}}, s["removed"]
assert s["enrich"]["gone"] == 1 and s["enrich"]["left"] == 0  # the card shows progress, not only a total
assert s["leads"] == 0  # every lead here is removed, so none counts as a unique lead
d.p.db = fake_db

# a resumed run is judged by its own log lines: an old "unusual traffic" line must not mark every resume blocked
d.save_state(run=saved, blocks={}, running=True)
(tmp / "scraper.log").write_text("Our systems have detected unusual traffic\n")
d.subprocess.run = fake_run(0)
d.Runner().scrape_once(saved, resume=True)
assert d.load_state()["run"] is None and d.load_state()["last_run"]["class"] == "ok", d.load_state()

# the run is cleared only after its results are loaded: a crash while loading keeps it for a resume
d.save_state(run=saved, blocks={})
real_ingest = d.p.ingest
d.p.ingest = lambda path, keys: (_ for _ in ()).throw(OSError("killed mid-ingest"))
try:
    d.Runner().scrape_once(saved, resume=False)
except OSError:
    pass
assert d.load_state()["run"] == saved
d.p.ingest = real_ingest

# one exception (leads.csv open in Excel, a truncated line) must not end the 24/7 loop
d.save_state(run=saved, blocks={}, running=True)
r = d.Runner()
tries = []


def flaky(run, resume):
    tries.append(1)
    if len(tries) == 1:
        raise PermissionError("leads.csv is open in Excel")
    r.stop.set()
r.scrape_once = flaky
d.PAUSE_MIN = 0
r.loop()
assert len(tries) == 2 and "PermissionError" in (tmp / "runner.log").read_text()

# state.json: concurrent writers never lose each other's changes, and no temp file is left behind
import threading
d.save_state(run=None)
writers = [threading.Thread(target=d.save_state, kwargs={f"k{i}": i}) for i in range(30)]
[t.start() for t in writers]
[t.join() for t in writers]
assert all(d.load_state().get(f"k{i}") == i for i in range(30)), d.load_state()
assert [f.name for f in tmp.glob("state*")] == ["state.json"]

# Stop pressed while planning: Docker is never launched
launched.clear()
d.subprocess.run = fake_run(0)
r = d.Runner()
grid.next_run = lambda con: (r.stop.set(), {**saved, "results": None})[1]
d.save_state(run=None, running=True)
r.loop()
assert not any(a[:2] == ["docker", "run"] for a in launched), launched

# a new run records its depth and concurrency; an orphaned container is removed before the launch
launched.clear()
r = d.Runner()
grid.next_run = lambda con: dict(saved)
r.scrape_once = (lambda orig: lambda run, resume: (orig(run, resume), r.stop.set()))(r.scrape_once)
r.loop()
assert launched[0][:3] == ["docker", "rm", "-f"] and launched[1][:2] == ["docker", "run"], launched
assert launched[1][launched[1].index("-depth") + 1] == str(d.DEPTH)

# a kept run whose location was removed from the targets is dropped, not resumed
launched.clear()
grid.targets = lambda: (["dentist"], [("London", "GB")])
r = d.Runner()
grid.next_run = lambda con: r.stop.set()
d.save_state(run=saved, running=True)
r.loop()
assert d.load_state()["run"] is None and not launched, launched
grid.targets = lambda: (["dentist"], [("Dubai", "AE")])

# Docker Desktop closed (and not installed where we look): no launch, no Google pause, no resume used up
launched.clear()
d.DOCKER_DESKTOP = tmp / "missing.exe"
d.save_state(run=saved, running=True, blocks={"google": {"score": 0, "pause_min": 30, "stopped": False, "kind": "ok"}})
d.subprocess.run = lambda args, **kw: launched.append(args) or Proc(1)
r = d.Runner()
seen = []
r.docker_ready = (lambda orig: lambda: (seen.append((orig(), r.phase)), r.stop.set())[0][0])(r.docker_ready)
r.loop()
assert seen == [(False, d.DOCKER_DOWN)] and launched == [["docker", "info"]], (seen, launched)
assert d.load_state()["run"] == saved and d.load_state()["blocks"]["google"]["kind"] == "ok"

# gosom can hang after "scrapemate exited" with its browsers still open: stop the container, count the run as done
import time
d.REAP_S = 0.05
stops = []


def hung_run(args, **kw):
    if args[:2] == ["docker", "stop"]:
        return stops.append(args) or Proc(0)
    if args[:2] != ["docker", "run"]:
        return Proc(0)
    kw["stdout"].write('{"message":"scrapemate exited"}\n')
    kw["stdout"].flush()
    (tmp / "raw-1.json").write_text('{"title": "x"}\n')
    for _ in range(100):
        if stops:
            return Proc(143)
        time.sleep(0.05)
    raise AssertionError("the hung container was never stopped")


d.save_state(run=saved, blocks={}, running=True)
d.subprocess.run = hung_run
d.Runner().scrape_once(saved, resume=False)
assert stops and d.load_state()["run"] is None and d.load_state()["last_run"]["class"] == "ok", d.load_state()

# sector dropdown: sectors.txt must survive save_targets unchanged (lowercase, no "#" or "|", no repeats)
opts = d.p.lines("sectors.txt")
assert len(opts) >= 40 and len(set(opts)) == len(opts), "sectors.txt has repeats"
assert all(o == o.lower() and "#" not in o and "|" not in o for o in opts), opts
print("all checks passed")
