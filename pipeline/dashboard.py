"""Lead Watch: live dashboard + the 24/7 scrape loop, with Start/Stop and target picking.

Run: python pipeline/dashboard.py  ->  http://localhost:8765
Knobs (env): CONCURRENCY (2), PAUSE_MIN (rest between cycles, 30), DEPTH (scroll depth per search, 10),
GRID_CELL_KM (4), ENRICH_PER_CYCLE (websites read per cycle, 300), LINKEDIN_PER_CYCLE (Brave searches, 30),
BRAVE_API_KEY (optional; enables LinkedIn names), BRAVE_MONTHLY_CAP (1000), OSM_PER_CYCLE (Overpass queries, 20),
FSQ_PER_CYCLE (Foursquare reads, 20), HF_TOKEN (optional; enables Foursquare after its free Hugging Face sign-up),
OVERTURE_PER_CYCLE (Overture Maps reads, 20; free, no account), COMPANIES_HOUSE_KEY (optional; UK owner names
from the free Companies House API), CH_PER_CYCLE (UK leads looked up, 100).
"""
import asyncio
import json
import math
import os
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import phonenumbers

import backoff
import enrich
import fsq
import overture
import geo
import grid
import companies_house
import linkedin
import osm
import process as p

PORT = 8765
IMAGE = "gosom/google-maps-scraper:v1.19.0"
CONTAINER = "leads-scraper"
REAP_S = 30  # how often to check the scraper log for a finished-but-hung gosom
DOCKER_DESKTOP = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
DOCKER_DOWN = "Docker Desktop is not running: open it and the run carries on"
CONCURRENCY = int(os.environ.get("CONCURRENCY", 2))  # keep low on a datacenter IP to avoid blocks
DEPTH = int(os.environ.get("DEPTH", 10))  # ~120 places per search; lower it if blocks become constant
ZOOM = 15  # gosom's default; its README gives no separate value for grid mode
MAX_RESUMES = 2  # a run that keeps crashing is dropped; its searches stay queued for a fresh try
PAUSE_MIN = float(os.environ.get("PAUSE_MIN", 30))
ENRICH_PER_CYCLE = int(os.environ.get("ENRICH_PER_CYCLE", 300))
LINKEDIN_PER_CYCLE = int(os.environ.get("LINKEDIN_PER_CYCLE", 30))
CH_PER_CYCLE = int(os.environ.get("CH_PER_CYCLE", 100))  # UK leads looked up in Companies House (~3 calls each)
OSM_PER_CYCLE = int(os.environ.get("OSM_PER_CYCLE", 20))
FSQ_PER_CYCLE = int(os.environ.get("FSQ_PER_CYCLE", 20))
OVERTURE_PER_CYCLE = int(os.environ.get("OVERTURE_PER_CYCLE", 20))  # spreads the monthly Brave credit over many cycles
PAGE = (Path(__file__).parent / "dashboard.html").read_bytes()
STATE_FILE = p.DATA / "state.json"
COUNTRIES = sorted(phonenumbers.SUPPORTED_REGIONS)
STATE_LOCK = threading.RLock()  # the loop and the 3 s dashboard poll both read and write state.json


def load_state():
    with STATE_LOCK:
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return {"running": False, "dups_total": 0, "last_run": None}


def save_state(**changes):
    with STATE_LOCK:
        state = {**load_state(), **changes}
        p.DATA.mkdir(exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(STATE_FILE)  # a crash mid-write never leaves half a file
        return state


def docker_args(run, results, resume):
    """The scraper command for a run. gosom resumes only with identical options, so -resume is the one change."""
    args = ["docker", "run", "--rm", "--name", CONTAINER,
            "-v", "gmaps-playwright-cache:/opt", "-v", f"{p.DATA}:/out", IMAGE,
            "-input", "/out/batch.txt", "-results", f"/out/{results}", "-json", "-email",
            "-depth", str(run.get("depth", DEPTH)), "-c", str(run.get("concurrency", CONCURRENCY)),
            "-exit-on-inactivity", "3m"]
    if run["mode"] == "grid":
        args += ["-grid-bbox", run["bbox"], "-grid-cell", str(run["cell_km"]), "-grid-strict", "-zoom", str(ZOOM)]
    return args + ["-resume"] if resume else args


def reap_hung(log_path, start, done, reaped):
    """gosom can print "scrapemate exited" and then hang with its browsers open: stop the container a minute later."""
    while not done.wait(REAP_S):
        if b"scrapemate exited" in log_path.read_bytes()[start:]:
            if not done.wait(2 * REAP_S):
                reaped.append(True)
                subprocess.run(["docker", "stop", "-t", "5", CONTAINER], capture_output=True)
            return


class Runner:
    """Scrape -> verify -> export, forever, until Stop. Survives restarts via state.json."""

    def __init__(self):
        self.stop = threading.Event()
        self.thread = None
        self.phase = "stopped"
        self.known_at_start = set()
        self.sources = {}  # last cycle's OpenStreetMap and Foursquare results, for the dashboard
        self.batch, self.limit, self.written = None, None, 0  # this Start's block in leads.xlsx

    def start(self, limit=None):
        """limit: stop once this many new leads are in (the sheet's block for this run holds exactly that many)."""
        if self.thread and self.thread.is_alive():
            return
        self.batch, self.limit, self.written = time.strftime("%Y-%m-%d %H:%M"), limit, 0
        self.stop.clear()
        save_state(running=True)
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def halt(self):
        save_state(running=False)
        self.stop.set()
        self.phase = "stopping"
        subprocess.run(["docker", "stop", "-t", "5", CONTAINER], capture_output=True)

    def loop(self):
        while not self.stop.is_set():
            try:
                run = load_state().get("run")  # unfinished run (Stop, crash, reboot): resume it as it was
                if run and not grid.wanted(run):  # its location, or every sector in it, left the targets
                    run = save_state(run=None)["run"]
                resume = bool(run)
                if not run:
                    self.phase = "planning"
                    run = grid.next_run(p.db())
                    if self.stop.is_set():  # Stop pressed while planning (geocoding): launch nothing
                        continue
                    if run:  # depth and concurrency go with the run: gosom resumes only with identical options
                        run = save_state(run={**run, "results": f"raw-{time.strftime('%Y%m%d-%H%M%S')}.json",
                                              "resumes": 0, "depth": DEPTH, "concurrency": CONCURRENCY})["run"]
                if run and not self.docker_ready():
                    wait = 60  # not Google's fault: no pause, no resume used up
                elif run:
                    self.scrape_once(run, resume)
                    if self.at_limit(p.db()):
                        continue
                    wait = load_state().get("blocks", {}).get("google", {}).get("pause_min", PAUSE_MIN) * 60
                else:
                    self.phase = "done"  # every sector x location searched; re-check for new targets often
                    wait = 30
                self.enrich_cycle(scraped=bool(run))  # runs even when Google is blocking: other hosts
            except Exception:  # leads.csv open in Excel, a truncated file, a locked DB: log it, rest, go on
                with open(p.DATA / "runner.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{traceback.format_exc()}\n")
                self.phase = "error, trying again after the pause (see data/runner.log)"
                wait = PAUSE_MIN * 60
            self.stop.wait(wait)
        if self.phase != backoff.STOP_MESSAGE and not self.phase.startswith("Stopped"):
            self.phase = "stopped"

    def at_limit(self, con):
        """With a limit set: once enough new leads wait for the sheet, export them now and stop."""
        if not self.limit or self.written + len(p.not_in_sheet(con)) < self.limit:
            return False
        self.phase = "exporting"
        self.written += p.export(self.batch, self.limit - self.written)
        save_state(running=False)
        self.stop.set()
        self.phase = (f"Stopped after {self.written} new leads" if self.written >= self.limit else
                      "Stopped: leads.xlsx is open in Excel. Close it and press Start; the new leads are kept")
        return True

    def docker_ready(self):
        """The Google scraper runs in Docker. If Docker Desktop is closed, open it and give it 2 minutes to come up."""
        up = lambda: subprocess.run(["docker", "info"], capture_output=True).returncode == 0
        if up():
            return True
        if DOCKER_DESKTOP.exists():
            self.phase = "starting Docker Desktop"
            subprocess.Popen([str(DOCKER_DESKTOP)])
            for _ in range(24):
                if self.stop.wait(5):
                    return False
                if up():
                    return True
        self.phase = DOCKER_DOWN
        return False

    def scrape_once(self, run, resume):
        out = p.DATA / run["results"]
        (p.DATA / "batch.txt").write_text("\n".join(run["inputs"]) + "\n", encoding="utf-8")
        self.known_at_start = {r[0] for r in p.db().execute("SELECT place_id FROM leads")}
        self.phase = "scraping"
        log_path = p.DATA / "scraper.log"
        start = log_path.stat().st_size if resume and log_path.exists() else 0
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)  # a container left by a crash blocks the name
        done, reaped = threading.Event(), []
        with open(log_path, "a" if resume else "w") as log:
            threading.Thread(target=reap_hung, args=(log_path, start, done, reaped), daemon=True).start()
            code = subprocess.run(docker_args(run, out.name, resume), stdout=log, stderr=subprocess.STDOUT).returncode
        done.set()
        code = 0 if reaped else code  # gosom had finished; only its shutdown hung
        stopped = self.stop.is_set()
        if not out.exists():  # no output at all: the scraper never started (Docker down), not a Google block
            kind = "error"
        else:
            log_text = log_path.read_bytes()[start:].decode("utf-8", "ignore")  # this attempt only: old block lines are history
            kind = backoff.classify_run(code, stopped, len(live_rows(out)), len(run["inputs"]), log_text)
        finished = kind == "ok" and code == 0 and not stopped
        if kind == "error":  # crashed: resume a couple of times, then start over later
            run = {**run, "resumes": run["resumes"] + 1}
        keep = None if finished or run["resumes"] > MAX_RESUMES else run  # blocked: resume after the pause
        if not stopped:
            block = backoff.after_run(load_state().get("blocks", {}).get("google", {}), kind, PAUSE_MIN)
            save_state(blocks={**load_state().get("blocks", {}), "google": block},
                       next_attempt=time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + block["pause_min"] * 60)))
            if block["stopped"]:
                save_state(running=False)
                self.stop.set()
        auto_stopped = not stopped and self.stop.is_set()
        if not out.exists():
            save_state(run=keep)
            self.phase = backoff.STOP_MESSAGE if auto_stopped else f"scraper failed (exit {code}), see data/scraper.log"
            return
        self.phase = "verifying emails"
        # Stopped or crashed mid-run: keep what was found, but leave the searches queued.
        new, dup = p.ingest(out, run["keys"] if finished else [])
        state = load_state()
        save_state(run=keep, dups_total=state.get("dups_total", 0) + dup,  # cleared only once its results are in
                   last_run={"file": out.name, "new": new, "dups": dup, "at": time.strftime("%H:%M"), "class": kind})
        self.phase = backoff.STOP_MESSAGE if auto_stopped else "resting"

    def enrich_cycle(self, scraped):
        """Add places from OpenStreetMap, Foursquare and Overture Maps, read business websites, search Brave for missing owners,
        then rewrite the CSV. Runs even while Google is blocking: these are other hosts."""
        if self.stop.is_set():
            return
        after = self.phase
        con = p.db()
        self.phase = "adding other sources"
        with httpx.Client() as client:
            self.sources = {"osm": osm.run_pending(con, client, OSM_PER_CYCLE, self.stop),
                            "foursquare": fsq.run_pending(con, client, FSQ_PER_CYCLE, self.stop),
                            "overture": overture.run_pending(con, client, OVERTURE_PER_CYCLE, self.stop)}
        added = any(s.get("new") or s.get("merged") for s in self.sources.values())
        if self.at_limit(con):
            return
        self.phase = "reading websites"
        crawled = asyncio.run(enrich.enrich_pending(con, ENRICH_PER_CYCLE, self.stop)).get("crawled")
        self.phase = "searching Companies House"
        with httpx.Client(base_url=companies_house.API) as client:
            registered = companies_house.lookup_pending(con, client, CH_PER_CYCLE, self.stop,
                                                        os.environ.get("COMPANIES_HOUSE_KEY")).get("looked_up")
        self.phase = "searching LinkedIn"
        with httpx.Client() as client:
            looked_up = linkedin.lookup_pending(con, client, LINKEDIN_PER_CYCLE, self.stop).get("looked_up")
        if scraped or added or crawled or registered or looked_up:
            self.phase = "exporting"
            self.written += p.export(self.batch, self.limit and self.limit - self.written)
            if self.at_limit(con):
                return
        if self.phase != backoff.STOP_MESSAGE:
            self.phase = after


runner = Runner()


def run_info(run, log_text):
    """Where the current run is: grid cells = rows x columns of the bbox for each sector, done = finished
    Maps search jobs in the scraper log."""
    if not run:
        return None
    info = {"location": run.get("location"), "mode": run["mode"], "bbox": run["bbox"], "area_km2": None,
            "cells_total": None, "cells_done": backoff.search_jobs(log_text)[0], "area": run.get("area")}
    if run["mode"] == "grid":
        s, w, n, e = map(float, run["bbox"].split(","))
        height = (n - s) * 111.32
        width = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
        cell = run["cell_km"]
        info["area_km2"] = round(grid.bbox_area_km2((s, w, n, e)))
        info["cells_total"] = math.ceil(height / cell) * math.ceil(width / cell) * len(run["inputs"])
    return info


def raw_files():
    """Scraper results, oldest first. gosom's -resume sidecar (raw-X.json.resume.json) is not one."""
    return sorted(f for f in p.DATA.glob("raw-*.json") if not f.name.endswith(".resume.json"))


def live_rows(path):
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # last line may still be mid-write
    return rows


def clear_feed():
    """Hide the places the feed shows now; the saved leads are untouched."""
    raws = raw_files()
    save_state(feed_cleared={"file": raws[-1].name if raws else None, "n": len(live_rows(raws[-1])) if raws else 0,
                             "rowid": p.db().execute("SELECT COALESCE(MAX(rowid), 0) FROM leads").fetchone()[0]})


SOURCE_NAMES = {"google_maps": "Google Maps", "osm": "OpenStreetMap", "foursquare": "Foursquare", "overture": "Overture Maps"}


def other_source_leads(con, n=60):
    """The newest leads first found by OpenStreetMap, Foursquare or Overture (since Clear): (raw, place_id, source, at)."""
    after = (load_state().get("feed_cleared") or {}).get("rowid", 0)
    return [(json.loads(raw or "{}"), pid, src.split()[0], at) for raw, pid, src, at in con.execute(
        "SELECT raw, place_id, sources, scraped_at FROM leads WHERE rowid > ? AND sources NOT LIKE 'google_maps%' "
        "ORDER BY rowid DESC LIMIT ?", (after, n))]


def since_clear(rows, name):
    cleared = load_state().get("feed_cleared") or {}
    return rows[cleared["n"]:] if cleared.get("file") == name else rows


def targets():
    locs = [l.rsplit(",", 1) for l in p.lines("cities.txt")] if (p.ROOT / "cities.txt").exists() else []
    return {"sectors": p.lines("niches.txt") if (p.ROOT / "niches.txt").exists() else [],
            "locations": [[c.strip(), cc.strip().upper()] for c, cc in locs],
            "options": p.lines("sectors.txt")}  # the sector dropdown; edit sectors.txt to change it


def save_targets(body):
    # one line, no "#!#" or "|" (they delimit gosom's input_id)
    clean = lambda s: " ".join(str(s).replace("#", " ").replace("|", " ").split())[:80]
    sectors = list(dict.fromkeys(clean(s).lower() for s in body.get("sectors", []) if clean(s)))
    locations = list(dict.fromkeys((clean(c), str(cc).upper()) for c, cc in body.get("locations", [])
                                   if clean(c) and str(cc).upper() in phonenumbers.SUPPORTED_REGIONS))
    (p.ROOT / "niches.txt").write_text("".join(s + "\n" for s in sectors), encoding="utf-8")
    (p.ROOT / "cities.txt").write_text("".join(f"{c},{cc}\n" for c, cc in locations), encoding="utf-8")


def stats():
    con = p.db()
    one = lambda sql: con.execute(sql).fetchone()[0]
    known = {r[0]: r[1:] for r in con.execute("SELECT place_id, email, email_status, owner_name, owner_source FROM leads")}
    raws = raw_files()
    current = live_rows(raws[-1]) if raws else []
    searches_done, searches_total = grid.progress(con)
    log = p.DATA / "scraper.log"
    # Google rows carry their file's time, other sources their insert time (both UTC), newest first
    google_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(raws[-1].stat().st_mtime)) if raws else ""
    google = [(e, e.get("place_id") or e.get("data_id") or e.get("link"), "google_maps", google_at)
              for e in reversed(since_clear(current, raws[-1].name)[-60:] if raws else [])]
    feed = []
    for e, key, source, _ in sorted(google + other_source_leads(con), key=lambda x: x[3], reverse=True)[:60]:
        email, status, owner_name, owner_source = known.get(key, (None,) * 4)
        owner = p.lead_owner(e, {"email": email, "owner_name": owner_name, "owner_source": owner_source})
        cc, sector, _ = p.split_input(e.get("input_id"))
        feed.append({
            "name": e.get("title"), "category": e.get("category"), "cc": cc, "sector": sector,
            "phone": p.pretty_phone(e.get("phone"), cc), "emails": e.get("emails") or [], "link": e.get("link"),
            "rating": e.get("review_rating"), "reviews": e.get("review_count"),
            "mobile": p.to_e164(e.get("phone"), cc),
            "email": email, "email_status": status, "owner": owner[0], "owner_source": owner[0] and owner[1],
            "dup": source == "google_maps" and runner.phase == "scraping" and key in runner.known_at_start,
            "key": key, "source": SOURCE_NAMES.get(source, source),
        })
    state = load_state()
    google = state.get("blocks", {}).get("google", {})
    used, cap = linkedin.usage(con)
    return {
        "run": run_info(state.get("run"), log.read_text(encoding="utf-8", errors="ignore") if log.exists() else ""),
        "health": {"last_class": (state.get("last_run") or {}).get("class"), "score": google.get("score", 0),
                   "pause_min": google.get("pause_min", PAUSE_MIN), "next_attempt": state.get("next_attempt"),
                   "message": backoff.STOP_MESSAGE if google.get("stopped") else None},
        "enrich": {"crawled": one("SELECT COUNT(*) FROM leads WHERE site_status NOT IN ('skipped')"),
                   "names": one("SELECT COUNT(*) FROM leads WHERE owner_source LIKE 'Website%'"),
                   "blocked": one("SELECT COUNT(*) FROM leads WHERE site_status = 'blocked'")},
        "linkedin": {"used": used, "cap": cap} if os.environ.get("BRAVE_API_KEY") else {"skipped": "no BRAVE_API_KEY"},
        "owners": {"total": one("SELECT COUNT(*) FROM leads WHERE owner_name IS NOT NULL"),
                   "companies_house": one("SELECT COUNT(*) FROM leads WHERE owner_source LIKE 'Companies House%'")
                   if os.environ.get("COMPANIES_HOUSE_KEY") else None},
        "sources": {"osm": one("SELECT COUNT(*) FROM leads WHERE instr(sources, 'osm')"),
                    "foursquare": one("SELECT COUNT(*) FROM leads WHERE instr(sources, 'foursquare')"),
                    "overture": one("SELECT COUNT(*) FROM leads WHERE instr(sources, 'overture')"),
                    "last": runner.sources,  # typed-in sectors with no categories.json entry skip these sources
                    "unmapped": [s for s in grid.targets()[0] if s not in osm.categories()]},
        "running": runner.thread is not None and runner.thread.is_alive(),
        "phase": runner.phase,
        "run_file": raws[-1].name if raws else None,
        "run_places": len(current),
        "run_with_email": sum(1 for e in current if e.get("emails")),
        "batch": p.lines("data/batch.txt") if (p.DATA / "batch.txt").exists() else [],
        "searches_total": searches_total, "searches_done": searches_done,
        "leads": one("SELECT COUNT(*) FROM leads"),
        "emails_ok": one(f"SELECT COUNT(*) FROM leads WHERE {p.VERIFIED}"),
        "mobiles": one("SELECT COUNT(*) FROM leads WHERE phone_e164 IS NOT NULL"),
        "both": one(f"SELECT COUNT(*) FROM leads WHERE {p.VERIFIED} AND phone_e164 IS NOT NULL"),
        "dups_total": state.get("dups_total", 0), "last_run": state.get("last_run"),
        "targets": targets(), "countries": COUNTRIES,
        "feed": feed,
    }


LATEST = {"body": b""}  # the page's status, recomputed in the background


def refresh_status():
    """Recompute the page's status. Requests return the latest copy, so a busy loop (an export of 80,000 leads,
    a country-wide load) never makes the page wait or pile up requests. A failure keeps the last good copy."""
    try:
        LATEST["body"] = json.dumps(stats()).encode()
    except Exception:
        with open(p.DATA / "runner.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} status\n{traceback.format_exc()}\n")


def refresh_forever(every=2):
    while True:
        refresh_status()
        time.sleep(every)


class Handler(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api":
            self.send(LATEST["body"] or json.dumps(stats()).encode())
        elif self.path.startswith("/api/country?cc="):  # size of an "All of <country>" job
            cc = self.path.split("=", 1)[1].upper()
            if cc not in phonenumbers.SUPPORTED_REGIONS:
                return self.send(b'{"error":"unknown country"}', code=404)
            try:
                self.send(json.dumps(geo.summary(geo.towns(cc))).encode())
            except OSError as e:  # first use downloads GeoNames; offline -> say so instead of hanging the page
                self.send(json.dumps({"error": f"GeoNames download failed: {e}"}).encode(), code=502)
        else:
            self.send(PAGE, "text/html")

    def do_POST(self):
        # Only this page may press the buttons: JSON content type forces a CORS preflight we never
        # answer, and any Origin header must be our own.
        origin = self.headers.get("Origin")
        if self.headers.get("Content-Type") != "application/json" or (
                origin and origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")):
            return self.send(b'{"error":"forbidden"}', code=403)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/start":
            n = body.get("stop_after")
            runner.start(n if isinstance(n, int) and n > 0 else None)
        elif self.path == "/stop":
            threading.Thread(target=runner.halt, daemon=True).start()  # docker stop takes a few seconds
        elif self.path == "/targets":
            save_targets(body)
        elif self.path == "/clear-feed":
            clear_feed()
        else:
            return self.send(b'{"error":"not found"}', code=404)
        self.send(b'{"ok":true}')

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    p.db().close()  # add any new columns now, before the loop and the page poll both open the database
    if load_state().get("running"):
        runner.start()  # resume after a reboot or crash
    refresh_status()
    threading.Thread(target=refresh_forever, daemon=True).start()
    print(f"Lead Watch on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()  # localhost only: lead data is private
