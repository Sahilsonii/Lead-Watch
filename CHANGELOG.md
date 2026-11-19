# Changelog

## In progress — read this first when picking the work back up

Kept up to date after every step, so the work can continue from any account or session.

**Where things stand (2026-11-20 01:50 IST)**
- 2026-11-20 02:03 IST: Phase A is **merged into `main`** (Sahil's call); the `feat/phase-a` branch is deleted. All test scripts pass on `main`: `pipeline/test_*.py`.
- A live test ran on 2026-11-20 (dentists, Dubai Marina): grid search, Stop then Start resume, website reading and the 20-column CSV all worked. Four bugs it found are fixed (see Phase A below).
- 2026-11-20 02:05 IST: the final whole-branch review of Phase A found 2 critical and 8 important problems. All 10 are fixed, each with a test that failed first; all 7 test scripts pass (see Phase A, "Fixed (final review)"). Its 11 small suggestions are listed as deferred in the task log, not done.
- Plan: `docs/superpowers/plans/2026-11-20-lead-watch-phase-a.md`. Design: `docs/superpowers/specs/2026-11-20-lead-watch-round-one-design.md`. The step-by-step task log is in `.superpowers/sdd/2026-11-20-lead-watch-phase-a/progress.md` (not in git).
- Backups of the lead database from before the live test: `data/leads.backup.db`, `data/leads.db.bak-phase-a`.
- 2026-11-20 01:55 IST: dark mode toggle added to the dashboard header (see Phase A, Added). Done and checked in the browser.

**Sector dropdown and whole-country search: both built** (see Unreleased)
- 2026-11-20 01:51 IST, Sahil: the dropdown holds about 50 **general** sectors, not 1,000 specific Google categories, kept in the project. The 61-sector list in `sectors.txt` is a first draft for Sahil to correct.
- 2026-11-20 02:40 IST: whole-country search built. The design, with Sahil's decisions, is in the round-one spec under "Design: whole-country search". Sahil changed the order on the way: **smallest areas first** (villages 1,000–15,000 people, towns to 100,000, cities, states, country) instead of "biggest state, biggest town first"; **single cities always run before any country**; and the user only picks the country, the rest runs by itself.
- 2026-11-20: a live scrape of a village-sized 4 km box worked: restaurants in Ar Rams (Ras Al Khaimah) gave 23 places, all inside the box, 22 with a phone; 4 search jobs, none failed, classified "ok".
- 2026-11-20, Sahil: "add every source and what was remaining", **free only** (no card). Done: OpenStreetMap, Foursquare (built, waiting on Sahil's Hugging Face sign-up), cross-source matching, the sector → category map, all 11 deferred review items and the Brave label. **Tripadvisor is not built:** its first page answered 403 with a DataDome bot check, and bot checks are never bypassed.
- Not live-tested yet: Foursquare on real data (needs access; a fake-token run confirmed the download path and that a refusal is handled), and a full 24/7 cycle with the new sources inside the running dashboard (OSM was run live against a copy of the database: Dubai dentists gave 28 new leads and 5 matches).
- **Next:** Sahil's Foursquare sign-up, then `python pipeline/fsq.py check AE` to correct any category labels that match nothing.
- Not included unless asked: a "new leads only" CSV.

## Unreleased

### Added
- **Whole-country search.** Pick a country with no cities and press Add: the "All of <country>" target sweeps the whole country by itself, one place per cycle, smallest areas first: villages (1,000–15,000 people), towns (15,000–100,000), cities (100,000+), each state, then the country. Villages and towns are grid-searched over a 4 km / 8 km box around GeoNames' own coordinates (no geocoding); cities, states and the country are geocoded as before. Single cities always run first, and a place already listed is searched once. Place names stay unique per country ("Rampur, Uttar Pradesh 2", "Dubai, United Arab Emirates" for the emirate), so no place is skipped as a false duplicate.
- Picking a country shows the size of the job (places per level × sectors, and the fewest days it can take); the run panel shows "Villages · 12 of 3,433 · Uttar Pradesh".
- Place data from GeoNames (CC BY 4.0): `cities1000`, `admin1CodesASCII` and `countryInfo`, downloaded once (~10 MB) into `data/geo/`. New `pipeline/geo.py` and `pipeline/test_geo.py`.

- **OpenStreetMap as a second source** (free, no account). Each sector × location is one Overpass query: a single city uses its geocoded area, a whole country one area query (split into its places if too big). Only places with a phone, website or email are kept. Busy or unreachable Overpass waits for the next cycle. New `pipeline/osm.py`.
- **Foursquare Open Source Places** (Apache 2.0, free; needs a one-time Hugging Face form and `HF_TOKEN`). Each country is copied once into `data/fsq/<CC>.parquet` with DuckDB; queries after that are local. Off, and labeled so on the dashboard, until the token is set. `python pipeline/fsq.py check CC` lists category labels that match nothing. New `pipeline/fsq.py`; `duckdb` added to `requirements.txt` (needed only for Foursquare).
- **Cross-source matching.** One lead per business, whichever source finds it first: same phone (landlines too), or same website domain (each within 500 m, so chain branches stay apart), or a name 85% alike within 150 m. A shared phone never joins two places whose websites differ (in the live run, two clinics in one building shared a reception number). A match fills empty fields only; a Google listing that finds an OSM/Foursquare lead takes it over, keeping one row. Existing leads get their match keys once, from stored data. New `lead_sources` table.
- **`categories.json`:** what each of the 61 sectors means on OpenStreetMap (every tag checked against taginfo usage) and Foursquare (label keywords, not yet checked on real data).
- **Dashboard:** OpenStreetMap and Foursquare cells (leads from each, last cycle's new and matched, or why it is off).
- `data/ATTRIBUTION.txt` is written next to `leads.csv` (OpenStreetMap ODbL, Foursquare Apache 2.0, GeoNames CC BY).

### Changed
- **Sources column** lists every source that found a lead (`google_maps osm`).
- **LinkedIn box** says Brave's API needs a card, instead of only "add BRAVE_API_KEY".
- **Sector dropdown.** In "What to scrape", the country comes first; once one is chosen, a **Sectors** dropdown appears with the general sectors from `sectors.txt` (edit that file to change the list). Picking one adds it as a removable chip and greys it out in the list; "Or type your own" still takes any sector. Every sector is still searched in every location.

### Fixed (deferred review items)
- Email MX lookups no longer run inside the website reader's event loop, so one slow DNS answer cannot stall other sites.
- A resumed run keeps the depth and concurrency it started with, even if the settings changed.
- Pressing Stop while the next run is being planned no longer launches the scraper once.
- A scraper container left behind by a crash is removed before the next launch (it blocked the name).
- `data/state.json` is written atomically and under a lock, so the loop and the dashboard never lose each other's changes.
- Site labels: a PDF homepage is "not_html" (was "timeout"), `image/svg+xml` is no longer read as a page, and a JavaScript-only page is "empty" (was "blocked"). "Websites read" counts every site a request went to.
- Cloudflare challenge pages that carry only `_cf_chl_opt` are recognized.
- `sites.google.com` business websites are read (they were treated as Google pages).
- A kept run whose location (or every sector) was removed from the targets is dropped instead of resumed.
- README and changelog describe the block score as it works (+1 per block, −0.5 per clean run, stop at 4), not "four blocks in a row". The README notes how to strip the WhatsApp column's spaces for `wa.me` links.

## 2026-11-20: Phase A — grid search, resume, website reading, back-off, LinkedIn via Brave

### Added
- **Grid search.** Locations up to 2,500 km² are geocoded once (OpenStreetMap Nominatim, cached) and searched as a grid of 4 km cells (`GRID_CELL_KM`), getting past Google's ~120-places-per-search limit. Bigger areas use plain searches.
- **Resume.** A run interrupted by Stop, a crash, a reboot or a block continues where it left off (`-resume`), instead of starting over. A run that crashes three times is dropped and its searches stay queued.
- **Block back-off and auto-stop.** Empty runs, mostly failed searches or Google's "unusual traffic" page count as a block. The pause doubles up to 4 hours. Each block adds 1 to a block score and each clean run takes 0.5 off; at 4 the loop stops with a clear message.
- **Polite website reading.** Up to 300 sites per cycle (`ENRICH_PER_CYCLE`), each read once: robots.txt and Crawl-delay obeyed, 1–2 s between pages, at most 4 pages (home plus contact/about/team, with a sitemap fallback), 1 MB per page, a bounded number of retries, and any bot-check page skipped. It finds emails (including "name [at] domain" forms and named mailto links), phones, social profiles and owner names (from structured data or team pages).
- **LinkedIn names via Brave Search** (optional, `BRAVE_API_KEY`). LinkedIn itself is never opened. Capped per month (`BRAVE_MONTHLY_CAP`, default 1000) and per cycle (`LINKEDIN_PER_CYCLE`, 30), and it stops on Brave's limit responses.
- `blocked_domains.txt`: website domains never read (government and health-service sites by default).
- **Export columns 19–20:** Social Links and Sources.
- **Dark mode toggle** (☾ / ☀) in the dashboard header. The page follows the system's light or dark setting until the toggle is used, then remembers the choice in that browser.
- **Run & health panel** on the dashboard: current location and grid cell progress, Google status, pause and next try, websites read, names found, sites blocked, and Brave usage.

### Changed
- Owner names now come from the best source available: website structured data > website team page > "Dr." in the business name > named email on the website > LinkedIn search result > email-address guess. Company-looking names are rejected.
- **Authenticity Verdict** is proportional: Verified at two thirds of the signals that apply, Likely genuine at one third. "Site matches listing" is a new signal when the website was read. Existing leads keep their labels.
- A Maps "website" that is really Facebook, Instagram or a directory moves to Social Links.
- More junk email domains are filtered (sentry, wixpress, cloudfront, w3 and similar site-code addresses).
- The CSV is rewritten at the end of each cycle, after enrichment.
- **Phone and WhatsApp numbers** are written in each country's standard format with the country code (`+971 4 566 2262`), instead of however Google spaced them. The live feed shows the same.

### Fixed (found in the first live run: 55 Dubai Marina dentists)
- One website with an unusual job-title format crashed the crawl and stopped the whole loop. Each site is now isolated; a site that fails is marked and not retried.
- A site with `Crawl-delay: 600` held up the cycle for over 10 minutes. Sites asking for more than 30 s between pages now get their homepage only.
- The dashboard read gosom's resume sidecar file as the results file and stopped updating.
- Greetings ("Hi Karen"), departments and job titles ("General Dentist") were taken as owner names.

### Fixed (final review)
- One "unusual traffic" line from an earlier attempt marked every later resume as blocked, until the loop stopped itself. Each attempt is now judged by its own log lines.
- A business website that redirected to LinkedIn or Facebook was followed there. Those redirects are now refused before any request; the link goes to Social Links.
- A run where every search finished cleanly but found nothing (a small area, a rare sector) counted as a block.
- Chains sharing one website had it read once per branch, several at a time. Now each website is read once per cycle.
- A LinkedIn name could be erased by a later website read, and lookups ran before the website was read. Lookups now wait for the website, and a site with no owner never erases a name.
- A rejected Brave key (or Brave errors) made every eligible lead call Brave. A rejected key now stops lookups, and errors count toward the per-cycle limit.
- One unexpected error (for example `leads.csv` open in Excel) ended the 24/7 loop. It is now logged to `data/runner.log` and the loop tries again after the pause.
- A brief OpenStreetMap outage was remembered forever, turning a city into plain searches. Outages are no longer cached.
- Accented owner names were cut short ("Zoë Anne Smith" became "Anne Smith"). They are now kept whole, or left empty when unreadable.
- A finished run was cleared before its results were loaded, so a crash in between lost the run. It is now cleared after loading.

### Removed
- `BATCH`: each cycle now covers one location's remaining sectors.

## 2026-11-19 (evening): Dashboard controls, targets, 18-column export

### Added
- **Start / Stop buttons** on the dashboard. The 24/7 scrape loop now runs inside the dashboard server (`pipeline/dashboard.py`). Stop shuts down the scraper container within seconds, keeps the places already found, and leaves the interrupted searches queued. The running state is saved in `data/state.json`, so after a reboot the loop resumes only if it was running.
- **"What to scrape" panel**: type sectors (one per line), pick a country from a full list, add cities (or leave them empty to search the whole country), then **Save targets**. Saved to `niches.txt` and `cities.txt`.
- **18-column export** in `data/leads.csv`: Business Name, Owner Name, Owner Source, Address/Location, Postcode, City, Region, Business Sector, Category, Phone, Email, WhatsApp, Website, Total Google Reviews, Rating, Short Brief, Date Added, Authenticity Verdict.
  - Owner Name is best effort: from a "Dr. …" in the business name, or a personal-looking email (marked "guess"). Google's own owner field is just the business account.
  - Authenticity Verdict lists its signals: owner-claimed listing, 10+ reviews, website, verified email, valid phone. Closed businesses are labeled Closed.
- Each search now carries its sector and city through the scraper (`#!#AE|dentist|Dubai`), so every lead knows which search found it.
- Duplicate counters on the dashboard, plus an "Already saved" tag in the live feed.

### Changed
- **Dedupe is always on**, at three levels: searches never repeat (compared on the search text), places are stored once per Google place id, and the export keeps one row per email and per phone (chains, duplicate listings).
- The WhatsApp column is the mobile number in international format: likely on WhatsApp, not checked.
- Raw scraper data is now stored with each lead, so new export columns can be filled from old scrapes.

### Removed
- **WhatsApp number checking** (the Baileys-based `whatsapp/` folder), to avoid the account-ban risk. Mobile numbers are flagged instead.
- `run.sh`. The dashboard server replaced it. The systemd unit now runs `pipeline/dashboard.py`.

## 2026-11-19: First version

- Google Maps scraping with gosom/google-maps-scraper v1.19.0 in Docker, with website email extraction.
- Email verification: junk filter, MX lookup, optional SMTP mailbox check (`SMTP_CHECK=1`).
- Phone normalization to +E.164 by country; mobile vs. landline detection.
- SQLite lead store (`data/leads.db`), CSV export, live dashboard at http://localhost:8765.
- Oracle Cloud Always Free + systemd setup for 24/7 running.
- First test: 16 Dubai dentists scraped, 5 verified emails, 3 mobile numbers.
