"""Lead Watch: live dashboard + the 24/7 scrape loop, with Start/Stop and target picking.

Run: python pipeline/dashboard.py  ->  http://localhost:8765
Knobs (env): BATCH (searches per cycle, 5), CONCURRENCY (2), PAUSE_MIN (rest between cycles, 30).
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import phonenumbers

import process as p

PORT = 8765
IMAGE = "gosom/google-maps-scraper:v1.19.0"
CONTAINER = "leads-scraper"
BATCH = int(os.environ.get("BATCH", 5))
CONCURRENCY = int(os.environ.get("CONCURRENCY", 2))  # keep low on a datacenter IP to avoid blocks
PAUSE_MIN = float(os.environ.get("PAUSE_MIN", 30))
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
            if p.next_batch(BATCH):
                self.scrape_once()
                wait = PAUSE_MIN * 60
            else:
                self.phase = "done"  # every sector x location searched; re-check for new targets often
                wait = 30
            self.stop.wait(wait)
        self.phase = "stopped"

    def scrape_once(self):
        out = p.DATA / f"raw-{time.strftime('%Y%m%d-%H%M%S')}.json"
        self.known_at_start = {r[0] for r in p.db().execute("SELECT place_id FROM leads")}
        self.phase = "scraping"
        with open(p.DATA / "scraper.log", "w") as log:
            code = subprocess.run(
                ["docker", "run", "--rm", "--name", CONTAINER,
                 "-v", "gmaps-playwright-cache:/opt", "-v", f"{p.DATA}:/out", IMAGE,
                 "-input", "/out/batch.txt", "-results", f"/out/{out.name}", "-json", "-email",
                 "-depth", "1", "-c", str(CONCURRENCY), "-exit-on-inactivity", "3m"],
                stdout=log, stderr=subprocess.STDOUT).returncode
        if not out.exists():
            self.phase = f"scraper failed (exit {code}), see data/scraper.log"
            return
        self.phase = "verifying emails"
        # Stopped or crashed mid-run: keep what was found, but leave the searches queued for a retry.
        new, dup = p.ingest(out, mark_done=(code == 0 and not self.stop.is_set()))
        p.export()
        state = load_state()
        save_state(dups_total=state.get("dups_total", 0) + dup,
                   last_run={"file": out.name, "new": new, "dups": dup, "at": time.strftime("%H:%M")})
        self.phase = "resting"


runner = Runner()


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
            "locations": [[c.strip(), cc.strip().upper()] for c, cc in locs]}


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
    raws = sorted(p.DATA.glob("raw-*.json"))
    current = live_rows(raws[-1]) if raws else []
    searches = p.all_searches()
    done = p.done_searches()
    feed = []
    for e in reversed(current[-60:]):
        key = e.get("place_id") or e.get("data_id") or e.get("link")
        email, status = known.get(key, (None, None))
        cc, sector, _ = p.split_input(e.get("input_id"))
        feed.append({
            "name": e.get("title"), "category": e.get("category"), "cc": cc, "sector": sector,
            "phone": e.get("phone"), "emails": e.get("emails") or [], "link": e.get("link"),
            "rating": e.get("review_rating"), "reviews": e.get("review_count"),
            "mobile": p.to_e164(e.get("phone"), cc),
            "email": email, "email_status": status,
            "dup": runner.phase == "scraping" and key in runner.known_at_start,
        })
    state = load_state()
    return {
        "running": runner.thread is not None and runner.thread.is_alive(),
        "phase": runner.phase,
        "run_file": raws[-1].name if raws else None,
        "run_places": len(current),
        "run_with_email": sum(1 for e in current if e.get("emails")),
        "batch": p.lines("data/batch.txt") if (p.DATA / "batch.txt").exists() else [],
        "searches_total": len(searches), "searches_done": sum(q.split(" #!#")[0] in done for q in searches),
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
