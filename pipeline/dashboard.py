"""Lead Watch: live dashboard + the 24/7 scrape loop, with Start/Stop and target picking.

Run: python pipeline/dashboard.py  ->  http://localhost:8765
Knobs (env): CONCURRENCY (2), PAUSE_MIN (rest between cycles, 30), DEPTH (scroll depth per search, 10),
GRID_CELL_KM (4), ENRICH_PER_CYCLE (websites read per cycle, 300), LINKEDIN_PER_CYCLE (Brave searches, 30),
BRAVE_API_KEY (optional; enables LinkedIn names), BRAVE_MONTHLY_CAP (1000).
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
import geo
import grid
import linkedin
import process as p

PORT = 8765
IMAGE = "gosom/google-maps-scraper:v1.19.0"
CONTAINER = "leads-scraper"
CONCURRENCY = int(os.environ.get("CONCURRENCY", 2))  # keep low on a datacenter IP to avoid blocks
DEPTH = int(os.environ.get("DEPTH", 10))  # ~120 places per search; lower it if blocks become constant
ZOOM = 15  # gosom's default; its README gives no separate value for grid mode
MAX_RESUMES = 2  # a run that keeps crashing is dropped; its searches stay queued for a fresh try
PAUSE_MIN = float(os.environ.get("PAUSE_MIN", 30))
ENRICH_PER_CYCLE = int(os.environ.get("ENRICH_PER_CYCLE", 300))
LINKEDIN_PER_CYCLE = int(os.environ.get("LINKEDIN_PER_CYCLE", 30))  # spreads the monthly Brave credit over many cycles
PAGE = (Path(__file__).parent / "dashboard.html").read_bytes()
STATE_FILE = p.DATA / "state.json"
COUNTRIES = sorted(phonenumbers.SUPPORTED_REGIONS)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"running": False, "dups_total": 0, "last_run": None}


def save_state(**changes):
    state = {**load_state(), **changes}
    p.DATA.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
    return state


def docker_args(run, results, resume):
    """The scraper command for a run. gosom resumes only with identical options, so -resume is the one change."""
    args = ["docker", "run", "--rm", "--name", CONTAINER,
            "-v", "gmaps-playwright-cache:/opt", "-v", f"{p.DATA}:/out", IMAGE,
            "-input", "/out/batch.txt", "-results", f"/out/{results}", "-json", "-email",
            "-depth", str(DEPTH), "-c", str(CONCURRENCY), "-exit-on-inactivity", "3m"]
    if run["mode"] == "grid":
        args += ["-grid-bbox", run["bbox"], "-grid-cell", str(run["cell_km"]), "-grid-strict", "-zoom", str(ZOOM)]
    return args + ["-resume"] if resume else args


class Runner:
    """Scrape -> verify -> export, forever, until Stop. Survives restarts via state.json."""

    def __init__(self):
        self.stop = threading.Event()
        self.thread = None
        self.phase = "stopped"
        self.known_at_start = set()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
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
                resume = bool(run)
                if not run:
                    self.phase = "planning"
                    run = grid.next_run(p.db())
                    if run:
                        run = save_state(run={**run, "results": f"raw-{time.strftime('%Y%m%d-%H%M%S')}.json",
                                              "resumes": 0})["run"]
                if run:
                    self.scrape_once(run, resume)
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
        if self.phase != backoff.STOP_MESSAGE:
            self.phase = "stopped"

    def scrape_once(self, run, resume):
        out = p.DATA / run["results"]
        (p.DATA / "batch.txt").write_text("\n".join(run["inputs"]) + "\n", encoding="utf-8")
        self.known_at_start = {r[0] for r in p.db().execute("SELECT place_id FROM leads")}
        self.phase = "scraping"
        log_path = p.DATA / "scraper.log"
        start = log_path.stat().st_size if resume and log_path.exists() else 0
        with open(log_path, "a" if resume else "w") as log:
            code = subprocess.run(docker_args(run, out.name, resume), stdout=log, stderr=subprocess.STDOUT).returncode
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
        """Read business websites, then search Brave for missing owners, then rewrite the CSV."""
        if self.stop.is_set():
            return
        after = self.phase
        con = p.db()
        self.phase = "reading websites"
        crawled = asyncio.run(enrich.enrich_pending(con, ENRICH_PER_CYCLE, self.stop)).get("crawled")
        self.phase = "searching LinkedIn"
        with httpx.Client() as client:
            looked_up = linkedin.lookup_pending(con, client, LINKEDIN_PER_CYCLE, self.stop).get("looked_up")
        if scraped or crawled or looked_up:
            self.phase = "exporting"
            p.export()
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
    known = {r[0]: r[1:] for r in con.execute("SELECT place_id, email, email_status FROM leads")}
    raws = raw_files()
    current = live_rows(raws[-1]) if raws else []
    searches_done, searches_total = grid.progress(con)
    log = p.DATA / "scraper.log"
    feed = []
    for e in reversed(current[-60:]):
        key = e.get("place_id") or e.get("data_id") or e.get("link")
        email, status = known.get(key, (None, None))
        cc, sector, _ = p.split_input(e.get("input_id"))
        feed.append({
            "name": e.get("title"), "category": e.get("category"), "cc": cc, "sector": sector,
            "phone": p.pretty_phone(e.get("phone"), cc), "emails": e.get("emails") or [], "link": e.get("link"),
            "rating": e.get("review_rating"), "reviews": e.get("review_count"),
            "mobile": p.to_e164(e.get("phone"), cc),
            "email": email, "email_status": status,
            "dup": runner.phase == "scraping" and key in runner.known_at_start,
        })
    state = load_state()
    google = state.get("blocks", {}).get("google", {})
    used, cap = linkedin.usage(con)
    return {
        "run": run_info(state.get("run"), log.read_text(encoding="utf-8", errors="ignore") if log.exists() else ""),
        "health": {"last_class": (state.get("last_run") or {}).get("class"), "score": google.get("score", 0),
                   "pause_min": google.get("pause_min", PAUSE_MIN), "next_attempt": state.get("next_attempt"),
                   "message": backoff.STOP_MESSAGE if google.get("stopped") else None},
        "enrich": {"crawled": one("SELECT COUNT(*) FROM leads WHERE site_status IN ('ok', 'blocked', 'refused', 'timeout')"),
                   "names": one("SELECT COUNT(*) FROM leads WHERE owner_source LIKE 'Website%'"),
                   "blocked": one("SELECT COUNT(*) FROM leads WHERE site_status = 'blocked'")},
        "linkedin": {"used": used, "cap": cap} if os.environ.get("BRAVE_API_KEY") else {"skipped": "no BRAVE_API_KEY"},
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


class Handler(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api":
            self.send(json.dumps(stats()).encode())
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
            runner.start()
        elif self.path == "/stop":
            threading.Thread(target=runner.halt, daemon=True).start()  # docker stop takes a few seconds
        elif self.path == "/targets":
            save_targets(body)
        else:
            return self.send(b'{"error":"not found"}', code=404)
        self.send(b'{"ok":true}')

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    if load_state().get("running"):
        runner.start()  # resume after a reboot or crash
    print(f"Lead Watch on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()  # localhost only: lead data is private
