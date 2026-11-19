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


def fake_run(code):
    def run(args, **kw):
        launched.append(args)
        (tmp / "raw-1.json").write_text("")
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

d.save_state(run=saved)
r = d.Runner()
r.stop.set()  # Stop pressed: docker exits non-zero, run is kept unchanged for the next Start
r.scrape_once(saved, resume=False)
assert d.load_state()["run"] == saved
print("all checks passed")
