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

**Next: whole-country search and a sector dropdown (being designed, nothing built yet)**
Agreed with Sahil so far:
- Choosing a country with no cities adds an "All of <country>" target, replacing today's single whole-country search.
- States, regions and towns come from GeoNames (free, CC BY): towns of 15,000+ people, downloaded once into `data/geo/`.
- Order: the biggest state (by population) first; inside each state, the biggest town first; one town per run with all sectors. Existing single cities stay; a town already listed is searched once. GeoNames neighborhood entries are skipped.
- The dashboard shows the counts when a country is picked (states, towns, × sectors = searches), and the Run & health panel shows "state N of M, town N of M".
- Each town is geocoded and grid-searched like Phase A.
- The sectors text box becomes a dropdown that opens after a country is chosen.
- 2026-11-20 01:51 IST, Sahil: the dropdown holds about 50 **general** sectors (food, restaurants, pubs, plumbing…), not 1,000 specific Google categories. The list is kept in the project; no outside category list is used. A draft list was proposed in chat; waiting for Sahil's corrections, then the full design is written up and reviewed.
- Not included unless asked: a "new leads only" CSV.

## 2026-11-20: Phase A — grid search, resume, website reading, back-off, LinkedIn via Brave

### Added
- **Grid search.** Locations up to 2,500 km² are geocoded once (OpenStreetMap Nominatim, cached) and searched as a grid of 4 km cells (`GRID_CELL_KM`), getting past Google's ~120-places-per-search limit. Bigger areas use plain searches.
- **Resume.** A run interrupted by Stop, a crash, a reboot or a block continues where it left off (`-resume`), instead of starting over. A run that crashes three times is dropped and its searches stay queued.
- **Block back-off and auto-stop.** Empty runs, mostly failed searches or Google's "unusual traffic" page count as a block. The pause doubles up to 4 hours, and four blocks in a row stop the loop with a clear message.
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
