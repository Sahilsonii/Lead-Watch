"""Self-check for the scraper command the runner builds. Run: python pipeline/test_dashboard.py"""
import dashboard as d

grid_run = {"mode": "grid", "bbox": "25.0,55.0,25.3,55.3", "cell_km": 4.0,
            "inputs": ["dentist #!#AE|dentist|Dubai"], "keys": ["grid:dentist|Dubai"]}
plain_run = {"mode": "plain", "bbox": None, "cell_km": None,
             "inputs": ["dentist in London #!#GB|dentist|London"], "keys": ["dentist in London"]}

args = d.docker_args(grid_run, "raw-1.json", resume=False)
for flag in ["-grid-bbox", "25.0,55.0,25.3,55.3", "-grid-cell", "4.0", "-grid-strict", "-zoom", "-depth", "10"]:
    assert flag in args, flag
assert args[args.index("-depth") + 1] == "10" and "-resume" not in args
assert "/out/raw-1.json" in args and "/out/batch.txt" in args

# gosom resumes only with identical options: -resume is the one difference
resumed = d.docker_args(grid_run, "raw-1.json", resume=True)
assert "-resume" in resumed and [a for a in resumed if a != "-resume"] == args

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
d.p.export = lambda: 0
d.p.db = lambda path=None: type("C", (), {"execute": lambda *a: []})()
ingested, launched = [], []


class Proc:
    def __init__(self, code):
        self.returncode = code


def fake_run(code, places=1):
    def run(args, **kw):
        launched.append(args)
        (tmp / "raw-1.json").write_text('{"title": "x"}\n' * places)
        return Proc(code)
    return run


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
r = d.Runner()
d.p.export = lambda: order.append("export") or r.stop.set()
r.scrape_once = lambda run, resume: order.append("scrape")
d.save_state(run=saved, running=True)
r.loop()
assert order == ["scrape", "enrich", "linkedin", "export"], order

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
print("all checks passed")
