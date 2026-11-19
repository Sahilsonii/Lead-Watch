# Lead Watch Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get more out of Google Maps: grid search with resume, polite website crawling for emails and owner names, block back-off, LinkedIn names via Brave search, and the export and dashboard changes that show them.

**Architecture:** The dashboard server's runner loop stays the single process. New single-purpose modules sit next to `process.py`:
- `grid.py` plans each run as one location over one bounding box.
- `backoff.py` classifies runs and keeps a per-source block score.
- `contacts.py` holds pure HTML parsing and extraction rules.
- `enrich.py` handles polite fetching.
- `linkedin.py` calls the Brave API.

Everything writes into the existing SQLite `leads` table through new columns.

**Tech Stack:** Python 3.14, stdlib (`html.parser`, `urllib.robotparser`, `asyncio`, `sqlite3`), `dnspython`, `phonenumbers`, new `httpx`; gosom/google-maps-scraper `v1.19.0` in Docker.

**Spec:** `docs/superpowers/specs/2026-11-20-lead-watch-round-one-design.md` (sections A1–A6). Phase B (Foursquare, OSM, Tripadvisor, cross-source matching) gets its own plan after this one ships, because it reuses `backoff.py`, `contacts.py` and `enrich.py` as built here.

## Global Constraints

- Free to run. Must work on Windows + Docker Desktop and on Oracle Cloud Always Free (ARM, Ubuntu).
- Dependencies: stdlib plus `dnspython`, `phonenumbers`, and `httpx` (new, pinned). No headless browser, no LLM, no stealth or fingerprint libraries.
- Never solve or bypass a CAPTCHA, challenge page or bot check. On a block: slow down or stop.
- Dashboard listens on `127.0.0.1` only.
- The 18 export columns keep their order and meaning. Two are appended: `Social Links`, `Sources`.
- Anti-goal: inventing data. A wrong owner name is worse than an empty one. Every owner value carries an Owner Source label. Ambiguous matches are skipped.
- One honest, generic User-Agent everywhere: `LeadWatch/0.2 (business contact research; respects robots.txt)`. No personal details in any outbound request.
- `BRAVE_API_KEY` is read from the environment only and never written to a file.
- American English in code comments, README and CHANGELOG.
- Tests follow the repo's style: assert-based scripts run as `.venv/Scripts/python pipeline/test_<name>.py` (on Linux `.venv/bin/python`). Network-free except the one existing MX check. HTTP is faked with `httpx.MockTransport`.
- Every task ends with a commit on `main`, with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Stop pressed mid-grid-run, then Start (or a reboot):** the same run resumes with `-resume` and identical options. It must not restart from cell 1, and it must not pick a different location. Test in Task 2.
2. **Nominatim finds nothing, errors, or returns a whole country:** the location falls back to plain searches without stalling the loop. Test in Task 1.
3. **The website is a PDF, an image, a 50 MB page, or a redirect to Facebook:** skip non-HTML, cap at 1 MB, and treat an aggregator after redirects like an aggregator up front. Test in Task 5.
4. **Arabic, Hindi or accented business and owner names:** matching and company-name checks use casefolded, whitespace-normalized text and never crash on non-ASCII. Test in Task 4.
5. **Brave answers 429 or 402 (credit gone) or the network drops:** stop lookups for this cycle, don't burn the counter on failed calls, and leave the lead eligible for next time. Test in Task 7.

---

### Task 0: Housekeeping (venv, launch config, httpx)

**Files:**
- Modify: `requirements.txt`
- Create: `.claude/launch.json`

- [ ] **Step 1:** Append `httpx==0.28.1` to `requirements.txt`.
- [ ] **Step 2:** Run `py -3 -m venv .venv` then `.venv/Scripts/pip install -r requirements.txt`. Expected: installs without error.
- [ ] **Step 3:** Run `cd pipeline && ../.venv/Scripts/python test_process.py`. Expected: `all checks passed`.
- [ ] **Step 4:** Create `.claude/launch.json` with one configuration:
  - name `dashboard`
  - `runtimeExecutable` `.venv/Scripts/python.exe`
  - `runtimeArgs` `["pipeline/dashboard.py"]`
  - port `8765`

  Start it with the preview tool. Expected: the page loads and shows the existing lead counts.
- [ ] **Step 5:** Commit `chore: recreate venv setup, add httpx and launch config`.

---

### Task 1: Grid run planner (A1)

**Files:**
- Create: `pipeline/grid.py`, `pipeline/test_grid.py`
- Modify: `pipeline/process.py` (`ensure_columns`, `ingest` signature)

**Interfaces:**
- Produces in `process.py`:
  - `ensure_columns(con, table: str, cols: dict[str, str]) -> None`: adds missing columns. It replaces the inline `raw` check and is used by later tasks.
  - `ingest(path, done_keys: list[str] = ()) -> tuple[int, int]`: replaces `mark_done`. It inserts exactly `done_keys` into `queries`. The CLI `ingest FILE` passes the batch lines' text before `" #!#"`.
- Produces in `grid.py`:
  - `CELL_KM = float(os.environ.get("GRID_CELL_KM", 4))`, `MAX_GRID_AREA_KM2 = 2500`, `USER_AGENT` (the Global Constraints string).
  - `geocode(name: str, cc: str, fetch=http_get_json) -> tuple[float, float, float, float] | None`: returns `(min_lat, min_lon, max_lat, max_lon)` from Nominatim `https://nominatim.openstreetmap.org/search?q=<name>&countrycodes=<cc>&format=json&limit=1`.
    - Nominatim's `boundingbox` is `[south, north, west, east]` as strings. Reorder it.
    - Return `None` on any error or empty result.
  - `bbox_area_km2(bbox) -> float`: equirectangular approximation, 111.32 km per degree, with the width scaled by `cos(mid_lat)`.
  - `location_bbox(con, name, cc, fetch=http_get_json) -> str | None`: cached in a new table `locations(name TEXT, cc TEXT, bbox TEXT, area_km2 REAL, PRIMARY KEY(name, cc))`.
    - `bbox` is stored as gosom's `"minLat,minLon,maxLat,maxLon"`, or NULL when geocoding failed.
    - Returns `None` when there is no bbox or the area is over `MAX_GRID_AREA_KM2`.
    - Sleeps 1.1 s after each live Nominatim call (its limit is 1 request/s).
  - `next_run(con, fetch=http_get_json) -> dict | None`: the first location (in `cities.txt` order) with pending sectors.
    - Returns `{"location", "cc", "mode": "grid"|"plain", "bbox": str|None, "cell_km": float|None, "inputs": [str], "keys": [str]}`.
    - Grid input: `f"{sector} #!#{cc}|{sector}|{location}"`, key `f"grid:{sector}|{location}"`, pending if that grid key is not done.
    - Plain input: today's `all_searches()` line, key = its text before `" #!#"`, pending if not done.
  - `pair_done(con, sector, location, cc) -> bool` and `progress(con) -> tuple[int, int]` (done, total sector×location pairs). For the dashboard. They use the cached `locations` row and never geocode.

- [ ] **Step 1:** Write `pipeline/test_grid.py` against an in-memory DB, with a fake `fetch`:
  - `geocode` reorders `{"boundingbox": ["25.0", "25.4", "55.0", "55.6"]}` to `(25.0, 55.0, 25.4, 55.6)`.
  - `geocode` returns `None` for `[]` and for a fetch that raises.
  - `bbox_area_km2((25.0, 55.0, 25.4, 55.6))` is between 2,700 and 2,750.
  - A bbox of `(25.0, 55.0, 25.3, 55.3)` (about 1,000 km²) gives `mode == "grid"`, with inputs `["dentist #!#AE|dentist|Dubai", ...]` and keys `["grid:dentist|Dubai", ...]`.
  - An oversized bbox, and a `None` geocode, both give `mode == "plain"` with today's input format.
  - After its keys are marked done, that location is skipped and the next one is returned. When all are done, `next_run` returns `None`.
  - The fake fetch is called once per location across two `next_run` calls (the cache works).
- [ ] **Step 2:** Run `.venv/Scripts/python pipeline/test_grid.py`. Expected: fails with `ModuleNotFoundError: No module named 'grid'`.
- [ ] **Step 3:** Implement `grid.py` and the two `process.py` changes. `next_run` reads targets through `process.lines()`. The `next-batch` CLI command keeps working unchanged.
- [ ] **Step 4:** Run `test_grid.py` and `test_process.py`. Expected: both pass.
- [ ] **Step 5:** Commit `feat(grid): plan one location per run over a Nominatim bbox`.

---

### Task 2: Runner uses grid runs, depth 10 and resume (A1, A2)

**Files:**
- Modify: `pipeline/dashboard.py` (`Runner.loop`, `Runner.scrape_once`)
- Create: `pipeline/test_dashboard.py`

**Interfaces:**
- Consumes: `grid.next_run`, `process.ingest(path, done_keys)`.
- Produces:
  - `DEPTH = int(os.environ.get("DEPTH", 10))`, `ZOOM` (see Step 1), `MAX_RESUMES = 2`.
  - `docker_args(run: dict, results: str, resume: bool) -> list[str]`: today's `docker run` list with these changes:
    - `-depth DEPTH` replaces `-depth 1`.
    - Grid mode adds `-grid-bbox <bbox> -grid-cell <cell_km> -grid-strict -zoom <ZOOM>`.
    - `-resume` is added when `resume` is true.
  - `state.json` gains `"run": {**run, "results": "<raw-file name>", "resumes": int}`. It stays while a run is unfinished.

- [ ] **Step 1:** Run `docker run --rm gosom/google-maps-scraper:v1.19.0 -h` and write down the exact spelling of the grid-bbox, grid-cell, grid-strict, zoom and resume flags, plus any recommended zoom for grid mode. Set `ZOOM` to that recommendation, or 15 if none is given. Fix the names in this task if they differ.
- [ ] **Step 2:** Write `test_dashboard.py` asserting:
  - `docker_args` for a grid run contains `-grid-bbox`, `25.0,55.0,25.3,55.3`, `-grid-cell`, `4.0`, `-grid-strict`, `-depth`, `10`, and contains no `-resume`.
  - With `resume=True`, `-resume` is present and the rest of the list is otherwise identical (gosom requires identical options).
  - A plain run has no grid flags.
- [ ] **Step 3:** Run it. Expected: fails (`docker_args` not defined).
- [ ] **Step 4:** Implement `docker_args` and these runner changes:
  - `scrape_once` takes `state["run"]` if present (resume), else asks `grid.next_run` and saves the run before launching.
  - `-input` points at `data/batch.txt`, which holds `run["inputs"]`.
  - On exit code 0 and no Stop: ingest with `done_keys=run["keys"]` and clear `run`.
  - On Stop: ingest with no keys and keep `run`.
  - On a non-zero exit: ingest with no keys and increment `resumes`. Once `resumes > MAX_RESUMES`, drop `run`; its keys stay pending and are retried fresh later.
- [ ] **Step 5:** Run `test_dashboard.py` and `test_process.py`. Expected: pass.
- [ ] **Step 6:** Commit `feat(runner): grid runs at depth 10 with resume after stop or crash`.

---

### Task 3: Block classification and back-off (A4)

**Files:**
- Create: `pipeline/backoff.py`, `pipeline/test_backoff.py`
- Modify: `pipeline/dashboard.py` (runner uses it)

**Interfaces:**
- Produces in `backoff.py` (Phase B reuses it per source):
  - `classify_run(code: int, stopped: bool, places: int, inputs: int, log_text: str) -> str`, returning `"ok" | "blocked" | "error"`.
    - Blocked when any of these hold:
      - `inputs > 0 and places == 0 and not stopped`;
      - more than half of the finished search jobs failed. Search jobs are the JSON log lines with `"message":"job finished"` whose `job` contains `/maps/search`; a failure is `"status"` other than `"success"`;
      - `"unusual traffic"` appears anywhere (case-insensitive);
      - both `infoDiv0` and `google.com/policies/terms` appear.
    - Otherwise `error` when `code != 0 and not stopped`; else `ok`.
  - `after_run(block: dict, kind: str, base_pause_min: float) -> dict`: takes and returns `{"score": float, "pause_min": float, "stopped": bool, "kind": str}`.
    - Blocked: `score += 1`; `pause_min = min(max(pause_min, base) * 2, 240)`.
    - Ok: `score = max(0, score - 0.5)`; `pause_min = base`.
    - Error: score unchanged; `pause_min = base`.
    - `stopped = score >= 4`.
  - `STOP_MESSAGE = "Google is blocking this IP. Stopped to protect it."`
- Runner:
  - Stores `state["blocks"]["google"]`.
  - On `blocked`: ingests with no keys and keeps `run`, so it resumes after the pause.
  - Waits `pause_min`.
  - On `stopped`: calls `halt()` and sets `phase = STOP_MESSAGE`.
  - Saves `state["next_attempt"]` (ISO time) before waiting.

- [ ] **Step 1:** Write `test_backoff.py`. Use log text built from real line shapes (copy two lines from `data/scraper.log`: a `"20 places found"` line and a `/maps/search` `"job finished"` success line). Assert:
  - `classify_run(0, False, 40, 2, ok_log) == "ok"`
  - `classify_run(0, False, 0, 2, ok_log) == "blocked"`
  - `classify_run(0, False, 40, 2, ok_log + "...unusual traffic...") == "blocked"`
  - A log with 2 failed and 1 successful search job is `"blocked"`.
  - `classify_run(1, False, 40, 2, ok_log) == "error"`
  - `classify_run(130, True, 0, 2, "") == "ok"`, because Stop is not a block.
  - Four blocks in a row give pauses 60, 120, 240, 240 and `stopped` on the fourth.
  - Block, ok, block, block, block reaches score 3.5 with `stopped` false; one more block sets `stopped` true.
- [ ] **Step 2:** Run it. Expected: fails (`No module named 'backoff'`).
- [ ] **Step 3:** Implement `backoff.py` and wire it into the runner.
- [ ] **Step 4:** Run all test scripts. Expected: pass.
- [ ] **Step 5:** Commit `feat(backoff): classify runs, double the pause on blocks, auto-stop at score 4`.

---

### Task 4: Contact extraction rules (A3 extract, EraSDR rules)

**Files:**
- Create: `pipeline/contacts.py`, `pipeline/test_contacts.py`, `blocked_domains.txt`
- Modify: `pipeline/process.py` (`JUNK_RE`)

**Interfaces:**
- Produces in `contacts.py`. All functions are pure (no network) and take `str` HTML:
  - `Page`, a dataclass with `text: str` (visible text, scripts and styles removed), `links: list[tuple[str, str]]` (href, link text), `ld_json: list[str]`, `base: str | None`. Built by `parse_page(html) -> Page` on stdlib `html.parser`.
  - `emails_in(page: Page) -> dict[str, str | None]`: lowercased email mapped to the person's name if one is attached. It reads:
    - `mailto:` hrefs, with the link text as the name;
    - `Name <email>` patterns;
    - obfuscated forms in which **"at" is bracketed** (`[at]`, `(at)`, `{at}`), with `dot` bracketed or bare;
    - loose `local@domain.tld`.

    Names go through `looks_like_company` and the generic-mailbox list, and are dropped if they fail.
  - `phones_in(page: Page, region: str) -> list[str]`: `tel:` hrefs plus `phonenumbers.PhoneNumberMatcher` over the text. Returns E.164 strings.
  - `json_ld(page: Page) -> list[dict]`: flattens `@graph` and top-level lists, and skips invalid JSON.
  - `ROLES = ("Owner", "Founder", "Co-Founder", "CEO", "Managing Director", "Director", "Principal", "Partner")`. Rank: Owner/Founder/Co-Founder > CEO/Managing Director > Principal > Director/Partner.
  - `owner_from_json_ld(objs: list[dict], business: str) -> tuple[str, str] | None` (name, role), from:
    - a `Person` with a `jobTitle` matching `ROLES`;
    - `founder` (a Person object or a name string) under an Organization or LocalBusiness;
    - an `employee` with a matching `jobTitle`.
  - `owner_from_text(text: str, business: str) -> tuple[str, str] | None`, from "Jane Smith, Founder", "Founder: Jane Smith", "Jane Smith – Managing Director", and "Dr. Jane Smith" next to a role word.
    - Takes the highest-ranked role found.
    - Returns `None` if more than one distinct name holds that top rank (ambiguous).
  - `looks_like_company(name: str, business: str) -> bool`. True for:
    - the EraSDR suffix list (limited, ltd, llc, inc, incorporated, plc, llp, gmbh, sarl, bv, pty, co, company, group, holdings, services, solutions, associates, partners, contractors, builders) as whole words;
    - all-caps text of 2+ words;
    - text equal to `business` after casefold and whitespace normalization.
  - `SOCIAL_HOSTS` (instagram.com, facebook.com, linkedin.com, tiktok.com, youtube.com, x.com, twitter.com) and `social_links(page: Page, page_url: str) -> list[str]`.
  - `AGGREGATOR_HOSTS` (`SOCIAL_HOSTS` + yelp, tripadvisor, trustpilot, pinterest, glassdoor, indeed, google, linktr.ee) and `is_aggregator(url: str) -> bool`, matching the host or any subdomain.
  - `load_blocked_domains() -> list[str]` reads `blocked_domains.txt`, one suffix per line, normalized the EraSDR way (lowercase, strip scheme, path and `www.`). `is_blocked_domain(url: str, suffixes: list[str]) -> bool`: an entry starting with `.` is a suffix match; any other entry matches the exact host or a subdomain.
  - `KEYWORDS = ("contact", "about", "team", "our-doctors", "doctors", "staff", "leadership", "founder", "enquiry", "get-in-touch", "reach-us", "book")`.
  - `candidate_links(page: Page, page_url: str) -> list[str]`:
    - resolves against `page.base` if set;
    - keeps same-site http(s) links only;
    - drops `mailto:`, `tel:` and `javascript:`;
    - keeps links whose URL or text contains a keyword;
    - ranks by keyword hit, then fewer path segments;
    - de-duplicates by URL without fragment.
  - `site_matches_listing(page_text: str, phone: str | None, business: str) -> bool`: the last 7 digits of the listing phone appear in the page's digits-only text, or the normalized business name appears in the normalized text.
  - `is_block_page(status: int, html: str) -> bool`. Write our own markers; don't copy crawl4ai's.
    - Challenge markers: Cloudflare `challenges.cloudflare.com` / `cf-chl`, DataDome `captcha-delivery.com`, PerimeterX `px-captcha`, Incapsula `_Incapsula_Resource`, Akamai "Access Denied" + "Reference #".
    - Also blocked: status 403 or 503 with a marker, or a 200 whose visible text is under 50 characters (an empty shell).
- `blocked_domains.txt`, seeded with `.gov`, `.gov.uk`, `.nhs.uk`, `.mod.uk`, `.police.uk`.
- `process.JUNK_RE` gains the hosts `googleapis`, `gstatic`, `cloudfront`, `schema`, `w3` (sentry and wixpress are already there). Add the tests to `test_process.py`.

- [ ] **Step 1:** Write `test_contacts.py` with small inline HTML fixtures. Assert:
  - `<a href="mailto:jane@smile.ae">Jane Smith</a>` gives `{"jane@smile.ae": "Jane Smith"}`.
  - `Jane Smith <jane@smile.ae>` gives the same.
  - `jane [at] smile [dot] ae` gives `jane@smile.ae`.
  - The plain sentence `meet us at clinic dot com` gives no email.
  - `<a href="mailto:info@smile.ae">SMILE DENTAL LTD</a>` gives `{"info@smile.ae": None}`.
  - A JSON-LD `@graph` holding a `Dentist` with `founder: {"@type": "Person", "name": "Sara Ali"}` gives `("Sara Ali", "Founder")`. Invalid JSON-LD is skipped without raising.
  - Text "Dr. Omar Khan, Founder ... Lina Haddad, Director" gives `("Dr. Omar Khan", "Founder")`.
  - Text "John Doe, Director ... Mary Roe, Director" gives `None` (ambiguous).
  - `looks_like_company("Smile Dental Group", "x")`, `looks_like_company("ABC TRADING", "x")` and `looks_like_company("عيادة الابتسامة", "عيادة  الابتسامة")` are all true. `looks_like_company("Sara Ali", "Smile Clinic")` is false.
  - `candidate_links` on a homepage with `/contact-us`, `/about`, `/blog/post-1`, `https://other.com/contact`, `mailto:x@y.z` and a `<base href="https://smile.ae/en/">` with a relative `team.html` returns `/contact-us`, `/about` and `https://smile.ae/en/team.html` only.
  - `is_aggregator("https://m.facebook.com/smile")` is true; `is_aggregator("https://smile.ae")` is false.
  - `is_blocked_domain("https://dha.gov.ae", [".gov.ae"])` is true. `is_blocked_domain("https://www.nhs.uk", [".nhs.uk"])` is true.
  - `site_matches_listing("Call 04 566 2262", "+971 4 566 2262", "x")` is true.
  - `is_block_page(403, "<html>...captcha-delivery.com...</html>")` is true. `is_block_page(200, "<html><body>" + "real text " * 20)` is false.
  - `phones_in` on `<a href="tel:+97145662262">` with region `AE` returns `["+97145662262"]`.
- [ ] **Step 2:** Run it. Expected: fails (`No module named 'contacts'`).
- [ ] **Step 3:** Implement `contacts.py`, `blocked_domains.txt` and the `JUNK_RE` change. Add `assert p.verify_email("x@cdn.cloudfront.net") == "invalid"` to `test_process.py`.
- [ ] **Step 4:** Run `test_contacts.py` and `test_process.py`. Expected: pass.
- [ ] **Step 5:** Commit `feat(contacts): extraction rules for emails, owners, social links and block pages`.

---

### Task 5: Polite website crawler (A3 fetching)

**Files:**
- Create: `pipeline/enrich.py`, `pipeline/test_enrich.py`

**Interfaces:**
- Consumes: everything in `contacts.py`; `USER_AGENT` from `grid.py`.
- Produces:
  - `RETRY_CODES = {408, 429, 500, 502, 503, 504, 522, 524}`, `MAX_RETRIES = 2`, `TIMEOUT_S = 10`, `MAX_BYTES = 1_000_000`, `SITES_AT_ONCE = 8`, `EXTRA_PAGES = 3`.
  - `async fetch(client: httpx.AsyncClient, url: str) -> tuple[int, str, str] | None` returns (status, final_url, html).
    - Streams the body and stops at `MAX_BYTES`.
    - Returns `None` if the content type is not HTML or XML.
    - Retries `RETRY_CODES` up to `MAX_RETRIES` times, honoring `Retry-After` in seconds, capped at 60.
    - Returns `None` on a timeout or a connection error.
  - `async robots_for(client, origin: str) -> urllib.robotparser.RobotFileParser | None`.
    - Fetches `/robots.txt` with httpx and feeds `parse()`.
    - 404 means allow all; 401 or 403 means disallow all.
    - 5xx or a timeout returns `None` (skip the site).
  - `async crawl_site(client, url: str, business: str, phone: str | None, region: str, suffixes: list[str]) -> dict`, returning:

    `{"status": "ok"|"blocked"|"refused"|"timeout"|"skipped", "emails": dict[str, str|None], "phones": list[str], "owner": tuple[str, str, str] | None, "social": list[str], "matches": bool, "pages": int}`

    where `owner` is (name, role, source) and source is "Website structured data", "Website team page" or "Website email link".
    - **Skipped** (social gets the URL when it is an aggregator): an aggregator URL (before or after redirects), a blocked domain, or no URL.
    - **Pages:** the homepage, then up to `EXTRA_PAGES` from `candidate_links`. If fewer than 3 candidates come back, read the sitemap: the robots `site_maps()`, else `/sitemap.xml`. Take `<loc>` URLs with a regex, keep same-site keyword matches, and add them.
    - **Politeness:** every URL passes `can_fetch(USER_AGENT, url)`. Pages are fetched one at a time per site, with `random.uniform(1, 2)` seconds between them, raised to `crawl_delay(USER_AGENT)` if robots.txt sets one.
    - **Blocks:** a block page marks the site `blocked` and stops; it is never parsed.
    - **Owner order:** JSON-LD over all pages, then team-page text, then a named email (from `emails_in`).
    - **Status when robots.txt answers 5xx or times out, or the homepage times out:** `timeout`.

- [ ] **Step 1:** Write `test_enrich.py` around `httpx.MockTransport` handlers (driven with `asyncio.run`). Assert:
  - A site whose homepage links `/contact` (holding `mailto:` with a name) and has a JSON-LD founder returns status `ok`, the email with its name, the owner with source "Website structured data", and `pages == 2`.
  - robots.txt answering 403 gives `refused`, and no page request is made (the handler records requests).
  - robots.txt answering 503 gives `timeout`, with no page request.
  - A homepage answering 503, 503, then 200 succeeds after 2 retries. 503 four times gives no page.
  - A homepage with a DataDome marker gives `blocked`, and its emails are not extracted.
  - `Disallow: /contact` in robots means `/contact` is never requested.
  - `application/pdf` gives no page, and nothing crashes.
  - A website that redirects to `https://www.facebook.com/x` gives `skipped`, with the Facebook URL in `social`.
  - A homepage with zero candidate links and a sitemap listing `/about-us` gets `/about-us` fetched.
  - Patch `asyncio.sleep` to a no-op in tests so they run instantly.
- [ ] **Step 2:** Run it. Expected: fails (`No module named 'enrich'`).
- [ ] **Step 3:** Implement `enrich.py`.
- [ ] **Step 4:** Run `test_enrich.py`. Expected: pass.
- [ ] **Step 5:** Commit `feat(enrich): polite multi-page website crawl with robots, retries and block skip`.

---

### Task 6: Store enrichment, proportional verdict, 20-column export (A3 storage, A5 export)

**Files:**
- Modify: `pipeline/process.py`, `pipeline/test_process.py`, `pipeline/enrich.py` (add `enrich_pending`)

**Interfaces:**
- Consumes: the `crawl_site` result dict from Task 5.
- Produces:
  - New `leads` columns via `ensure_columns`: `owner_name TEXT, owner_role TEXT, owner_source TEXT, social_links TEXT, site_status TEXT, site_matches INTEGER, enriched_at TEXT, linkedin_url TEXT, linkedin_checked_at TEXT, sources TEXT DEFAULT 'google_maps'`.
  - `save_enrichment(con, place_id: str, result: dict) -> None`:
    - Re-picks the email with `best_email()` over the union of the Maps emails and the site emails.
    - Stores the owner, unless `looks_like_company` rejects it.
    - Stores social links space-separated, and `site_status`, `site_matches` and `enriched_at`.
    - When the lead had no phone, uses the site's first phone and sets `phone_e164` through `to_e164`.
  - `lead_owner(e: dict, r) -> tuple[str, str]`, in priority order:
    1. stored owner with source "Website structured data";
    2. "Website team page";
    3. `owner_of`'s "Dr." business-name rule;
    4. "Website email link";
    5. "LinkedIn search result" (Task 7);
    6. `owner_of`'s "Email address (guess)".

    `lead_row` uses it.
  - `verdict(e, r)`, changed to proportional scoring:
    - Signals that **apply**: owner-claimed, 10+ reviews (both Google; they apply when `sources` contains `google_maps`), website, verified email, valid phone, and "site matches listing" (applies only when `site_status == 'ok'`).
    - Verified when hits ≥ 2/3 of the applicable signals; Likely genuine when ≥ 1/3; else Weak. Closed stays Closed.
  - The website shown and scored is the Maps website unless `is_aggregator(website)`. In that case Website is empty and the URL is in Social Links.
  - `COLUMNS` gains `"Social Links", "Sources"`. `lead_row` appends `social_links` (plus `linkedin_url`) and `sources`.
  - In `enrich.py`: `async enrich_pending(con, limit: int, stop: threading.Event) -> dict`.
    - Selects leads where `enriched_at IS NULL`, ordered by verified email or mobile first.
    - Runs `crawl_site` under `asyncio.Semaphore(SITES_AT_ONCE)`.
    - Passes each result to `save_enrichment`.
    - Checks `stop` between sites.
    - Returns `{"crawled": n, "names": n, "blocked": n}`.

- [ ] **Step 1:** Add to `test_process.py`. The existing verdict asserts must still pass unchanged. New asserts:
  - With 3 of 5 applicable hits, `verdict` starts with `Likely genuine`.
  - With 4 of 6 applicable hits (site crawled, matches), it starts with `Verified`.
  - `lead_owner` prefers a stored "Website team page" owner over a "Dr." business-name match, and prefers the "Dr." match over a "Website email link" owner.
  - `save_enrichment` with owner `("SMILE DENTAL LTD", "Owner", "Website team page")` stores no owner.
  - A lead whose Maps website is `https://www.facebook.com/smile` exports an empty Website and has the URL in Social Links.
  - `len(COLUMNS) == 20` and `COLUMNS[:18]` equals the old list exactly.
  - `enrich_pending` over 2 un-enriched leads, with `crawl_site` patched to return a fixed result, sets `enriched_at` on both and returns `crawled == 2`. Run it a second time: `crawled == 0` (each site is crawled once).
- [ ] **Step 2:** Run it. Expected: the new asserts fail.
- [ ] **Step 3:** Implement the `process.py` changes and `enrich_pending`.
- [ ] **Step 4:** Run all test scripts. Expected: pass.
- [ ] **Step 5:** Commit `feat(export): website owners, proportional verdict, Social Links and Sources columns`.

---

### Task 7: LinkedIn names via Brave Search (A6)

**Files:**
- Create: `pipeline/linkedin.py`, `pipeline/test_linkedin.py`

**Interfaces:**
- Consumes: the `leads` columns from Task 6; `contacts.looks_like_company`.
- Produces:
  - `BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"`, with headers `X-Subscription-Token: $BRAVE_API_KEY`, `Accept: application/json`, `User-Agent: USER_AGENT`; params `q`, `count=5`.
  - `MONTHLY_CAP = int(os.environ.get("BRAVE_MONTHLY_CAP", 1000))`, with the table `linkedin_usage(month TEXT PRIMARY KEY, used INTEGER)`.
  - `usage(con) -> tuple[int, int]` returns (used this month, cap).
  - `query_for(name: str, city: str) -> str` returns `f'"{name}" {city} site:linkedin.com'`.
  - `parse_title(title: str) -> tuple[str, str]` returns (name, role). It splits on ` - `, ` – ` or ` | `, and drops a trailing `LinkedIn`.
  - `pick(results: list[dict], business: str) -> tuple[str, str | None, str | None] | None` returns (url, name, role).
    - Only `linkedin.com/in/` and `linkedin.com/company/` URLs count, and only when the normalized business name appears in the result's title or description.
    - `/in/` results come first, giving a name and role.
    - A company page gives the URL only.
    - Names rejected by `looks_like_company` give the URL only.
  - `lookup_pending(con, client: httpx.Client, limit: int, stop: threading.Event) -> dict` returns `{"looked_up": n, "names": n, "skipped": reason|None}`.
    - With no `BRAVE_API_KEY`, returns `skipped="no BRAVE_API_KEY"` and makes no call.
    - Eligible leads: `linkedin_checked_at IS NULL`, the owner is empty or only an "Email address (guess)", and the lead has a verified email or a mobile. Ordered by both channels first, then most reviews.
    - Counts a call in usage only after a 200. Stops at the cap. Waits 1.1 s between calls.
    - A 429 or 402 stops the cycle (`skipped="brave limit"`) and leaves that lead unchecked.
    - On a match, stores `linkedin_url` and, when a person was found, the owner with source "LinkedIn search result".
    - Sets `linkedin_checked_at` on every 200, match or not.

- [ ] **Step 1:** Write `test_linkedin.py` with `httpx.MockTransport` and an in-memory DB. Assert:
  - No key gives no request.
  - `parse_title("Sara Ali - Founder - Smile Clinic | LinkedIn") == ("Sara Ali", "Founder")`.
  - `pick` rejects a profile that doesn't mention the business, and accepts one that does.
  - With the cap at 2 and 3 eligible leads, exactly 2 requests are made and usage shows 2.
  - A 402 response stops after 1 request, usage stays 0, and the lead stays eligible.
  - A lead whose owner came from "Website team page" is never looked up.
- [ ] **Step 2:** Run it. Expected: fails (`No module named 'linkedin'`).
- [ ] **Step 3:** Implement `linkedin.py`.
- [ ] **Step 4:** Run all test scripts. Expected: pass.
- [ ] **Step 5:** Commit `feat(linkedin): owner names from Brave search results, capped monthly, never touching LinkedIn`.

---

### Task 8: Runner integration and dashboard (A5 dashboard)

**Files:**
- Modify: `pipeline/dashboard.py`, `pipeline/dashboard.html`

**Interfaces:**
- Consumes:
  - `enrich.enrich_pending(con, limit, stop)` with `ENRICH_PER_CYCLE = int(os.environ.get("ENRICH_PER_CYCLE", 300))`;
  - `linkedin.lookup_pending(con, client, limit, stop)` and `linkedin.usage(con)`;
  - `grid.progress(con)`;
  - `state["blocks"]["google"]`, `state["run"]`, `state["next_attempt"]`.
- Cycle order: scrape → ingest → classify and back off → enrich (`asyncio.run`) → LinkedIn lookups → export → rest. Enrichment and lookups still run when Google is blocked; they hit other hosts.
- New `/api` fields:
  - `run`: `{location, mode, bbox, area_km2, cells_total, cells_done}`, or null. `cells_total = ceil(height_km / cell) * ceil(width_km / cell)`; `cells_done` is the count of finished `/maps/search` jobs in `scraper.log`.
  - `health`: `{last_class, score, pause_min, next_attempt, message}`.
  - `enrich`: `{crawled, names, blocked}`, cumulative from the DB.
  - `linkedin`: `{used, cap}` or `{"skipped": "no BRAVE_API_KEY"}`.
  - `searches_done`/`searches_total` now come from `grid.progress`.

- [ ] **Step 1:** Load the `interface-design` skill. Match `dashboard.html`'s existing tokens, IBM Plex type and dark/light handling. No new visual system.
- [ ] **Step 2:** Implement the runner order and the `/api` fields. Add one "Run & health" panel showing:
  - location, mode, bbox and area;
  - "cell N of M" (or places so far when plain);
  - last run class, current pause and next attempt;
  - the stop message when auto-stopped;
  - sites crawled, names found and sites blocked;
  - LinkedIn usage, or "Add BRAVE_API_KEY to enable".
- [ ] **Step 3:** Start the `dashboard` preview, fetch `/api`, and check that every new field is present and the panel renders with no console errors, in both light and dark.
- [ ] **Step 4:** Run all test scripts. Expected: pass.
- [ ] **Step 5:** Commit `feat(dashboard): run, health, enrichment and LinkedIn panels`.

---

### Task 9: Live smoke run, README and CHANGELOG

**Files:**
- Modify: `README.md`, `CHANGELOG.md`

- [ ] **Step 1:** Back up `data/leads.db` to `data/leads.backup.db`. Set `PAUSE_MIN=1`. Add a small test target (sector `dentist`, location `Dubai Marina, AE`) on the dashboard, then press Start. Expected:
  - The location is geocoded.
  - Mode is `grid` with an area under 2,500 km².
  - gosom runs with grid flags.
  - Places arrive in the feed.
- [ ] **Step 2:** Press Stop mid-run, then Start. Expected: `scraper.log` shows the same run continuing via `-resume`, and the results file keeps growing rather than starting over.
- [ ] **Step 3:** Let the cycle finish. Expected:
  - `enrich` shows sites crawled.
  - `data/leads.csv` has 20 columns and at least one owner with a Website source.
  - The run is classified `ok`.
- [ ] **Step 4:** Update the README:
  - grid search;
  - the knobs `DEPTH`, `GRID_CELL_KM`, `ENRICH_PER_CYCLE`, `BRAVE_MONTHLY_CAP`, `BRAVE_API_KEY`;
  - `blocked_domains.txt`;
  - the two new columns;
  - the verdict rule;
  - back-off and auto-stop;
  - LinkedIn via Brave, with the GDPR note that names are personal data;
  - the human step of creating a Brave key and a spending limit.

  Add a dated CHANGELOG entry for Phase A.
- [ ] **Step 5:** Restore the user's real targets if the test target was added. Commit `docs: Phase A README and changelog`.
