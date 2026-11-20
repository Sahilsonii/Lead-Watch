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
- Foursquare checked on real data: access granted, the UAE extract is 11 MB, and `fsq.py check AE` found 21 sectors with keywords that are not Foursquare category names. All 21 now use names from Foursquare's own category list; the check comes back clean and the 61 sectors match 33,348 UAE places. Beauty salon is thin (46): Foursquare files most beauty salons under the general "Health and Beauty Service", which also holds hair salons, spas and dry cleaners, so it is left out.
- 2026-11-20, Sahil: "build Overture". Done: Overture Maps places as a fourth source (free, no account). Live on a copy of the database with the final rules: Dubai dentists 661 new leads (472 with an email) and 50 matches; Dubai hotels, restaurants and fast food 10,785 new and 915 matches; `overture.py check AE` clean. The live data showed shared "websites" (WhatsApp links, short links, booking platforms, hotel brands), so matching changed: a shared website with two different phones is two businesses. Measured: among Dubai hotels and restaurants this keeps 497 same-website pairs apart (restaurants inside one hotel or mall, sister hotels), at the cost of about 10 duplicate dentists out of 661 (one clinic listing a landline and a mobile). Known limits: countries crossing the date line (US, Russia, New Zealand) have a 360-degree box, so their first Overture download reads much more of the world (still correct, slower). Also: listings that share both a phone and a website (OYO apartments behind OYO's call center, the hotels of one resort switchboard) become one lead, since nothing in the data separates them.
- Not live-tested yet: a full 24/7 cycle with the new sources inside the running dashboard (OSM was run live against a copy of the database: Dubai dentists gave 28 new leads and 5 matches).
- **Next:** run the 24/7 dashboard with `HF_TOKEN` set and watch one full cycle.
- 2026-11-20 18:50 IST, Sahil asked to add Trustpilot. Re-checked and **not added**: its robots.txt says `User-agent: *` / `Disallow: /` (only ~40 named bots such as Googlebot may crawl), and one polite category-page request got a 403 with an AWS WAF bot check. Both are hard stops under the robots.txt and no-bypass rules.
- Not included unless asked: a "new leads only" CSV.
- 2026-11-20 19:25 IST, Sahil ("as per suggestions add", after a Google Maps scraping tutorial video): **Best Way to Reach** column added to the CSV and Excel export (see Unreleased). 19:20 IST: a Google Sheet sync was built (633d49e), then reverted at 19:24 on Sahil's call: "instead we can add in excel". Built instead: `LEADS_XLSX` puts the Excel file in a OneDrive folder, which OneDrive shares online (see Unreleased). This PC runs OneDrive for Business. 19:45 IST, Sahil: "Yes, set it". Done: the folder `OneDrive - New Era AI\Lead Watch` is created, `LEADS_XLSX` is set as a user environment variable to `leads.xlsx` in it, and the current `data/leads.xlsx` was copied there. OneDrive had it queued for upload ("pending") behind another folder still uploading. **Still to do:** restart the dashboard (it was mid-scrape at 19:45, so it was left running and still writes to `data/`), and Sahil shares the folder with the team, view only.
- 2026-11-20 19:53 IST, Sahil: "restart the dashboard ... it was stuck, start with new again". The cause, gosom hanging after it finished, is fixed (see Unreleased, Fixed) (test first; all 13 test scripts pass). The old dashboard and the hung container were stopped, the stuck run was cleared so it starts fresh, and the dashboard was restarted with `LEADS_XLSX` (OneDrive) and `HF_TOKEN` set.
- 2026-11-20 19:58 IST, Sahil: "add country column in data too". Done: a Country column after Region (see Unreleased, Added); all 13 test scripts pass. The first search after the restart finished cleanly in about 3 minutes.
- 2026-11-20 19:59 IST, Sahil asked if the 28,474 carpenter leads are real. Checked: all are UK carpenters, all but 1 from Foursquare/Overture whole-country loads; 99.5% have a valid UK phone format; a few are spam or mis-tagged. Offered a freshness filter (drop Foursquare places not refreshed in years); Sahil: **not now**.
- 2026-11-20 20:10 IST, Sahil: "can't see leads from Google Maps or websites in the file", "should be country specific leads only". Google was answering with Indian results (fixed, see Unreleased, Fixed); every stored lead is in the UK (the box filter and the open-data country filters held). Website data was in leads.csv; leads.xlsx (OneDrive) was stale since 17:28 and is now rebuilt with Best Way to Reach and Country. Dashboard restarted on the fix.
- 2026-11-20 20:22 IST: live check of the Google fixes (place name in the search, country filter instead of the village box): one search (carpenter, Abbey Dore) gave 80 new leads and matched 40 open-data leads; Google leads went from 1 to 101, all in the UK. Dashboard restarted on 6c3453f.
- 2026-11-21 02:45 IST, Sahil: website reading looked stopped. It wasn't (the 20:40 seen was UTC = 02:10 IST); it was idle 95% of the time behind Google's pause. Sahil: read through the pause, drop dead-website leads, keep only running businesses with a phone, and add a link a person can check. All done (see Unreleased, Added). 34 of 40 sampled "timeout" sites had no DNS record; the 931 old timeouts are queued to be re-read.
- Still open from 02:23: which more fields and open datasets to add for marketing (asked how Sahil will market: email, WhatsApp, calls, ads).
- 2026-11-21 02:55 IST: Removed card checked live in the browser: 29,000 unique (live) leads; 237 removed (167 no phone, 70 website gone) from 101 Overture, 73 Foursquare, 33 Google, 30 OpenStreetMap. The gone count grows as the ~10,000 unread and 931 re-queued sites are read. Dashboard restarted on 0d9683a.

## Unreleased

### Added
- `LEADS_XLSX` sets where the Excel file is written, such as a shared OneDrive folder, so the team can open it online. The file is rebuilt on each export, so it is shared view only.
- **Best Way to Reach** export column, after WhatsApp: WhatsApp if there is a mobile, else Email if it is verified, else Call, else Website, else Social media. The export now has 21 columns.
- **Whole-country search.** Pick a country with no cities and press Add: the "All of <country>" target sweeps the whole country by itself, one place per cycle, smallest areas first: villages (1,000–15,000 people), towns (15,000–100,000), cities (100,000+), each state, then the country. Villages and towns are grid-searched over a 4 km / 8 km box around GeoNames' own coordinates (no geocoding); cities, states and the country are geocoded as before. Single cities always run first, and a place already listed is searched once. Place names stay unique per country ("Rampur, Uttar Pradesh 2", "Dubai, United Arab Emirates" for the emirate), so no place is skipped as a false duplicate.
- Picking a country shows the size of the job (places per level × sectors, and the fewest days it can take); the run panel shows "Villages · 12 of 3,433 · Uttar Pradesh".
- Place data from GeoNames (CC BY 4.0): `cities1000`, `admin1CodesASCII` and `countryInfo`, downloaded once (~10 MB) into `data/geo/`. New `pipeline/geo.py` and `pipeline/test_geo.py`.

- **OpenStreetMap as a second source** (free, no account). Each sector × location is one Overpass query: a single city uses its geocoded area, a whole country one area query (split into its places if too big). Only places with a phone, website or email are kept. Busy or unreachable Overpass waits for the next cycle. New `pipeline/osm.py`.
- **Foursquare Open Source Places** (Apache 2.0, free; needs a one-time Hugging Face form and `HF_TOKEN`). Each country is copied once into `data/fsq/<CC>.parquet` with DuckDB; queries after that are local. Off, and labeled so on the dashboard, until the token is set. `python pipeline/fsq.py check CC` lists category labels that match nothing. New `pipeline/fsq.py`; `duckdb` added to `requirements.txt` (needed only for Foursquare and Overture).
- **Cross-source matching.** One lead per business, whichever source finds it first: same phone (landlines too), or same website domain (each within 500 m, so chain branches stay apart), or a name 85% alike within 150 m. A shared phone never joins two places whose websites differ (in the live run, two clinics in one building shared a reception number). A match fills empty fields only; a Google listing that finds an OSM/Foursquare lead takes it over, keeping one row. Existing leads get their match keys once, from stored data. New `lead_sources` table.
- **`categories.json`:** what each of the 61 sectors means on OpenStreetMap (every tag checked against taginfo usage) and Foursquare (category names from Foursquare's own category list, checked against UAE data).
- **Overture Maps** (CDLA Permissive 2.0, free, no account). The latest release is found through Overture's public catalog; each country is copied once into `data/overture/<CC>.parquet` with DuckDB, using the country's box from Overture's own boundaries so most of the world's files are skipped. Open places at confidence 0.5 or higher with a phone, website or email are kept. `python pipeline/overture.py check CC` lists ids that match nothing. New `pipeline/overture.py` and `pipeline/test_overture.py`; `categories.json` gains an `overture` list for all 61 sectors (ids taken from real UAE data).
- **Dashboard:** OpenStreetMap, Foursquare and Overture Maps cells (leads from each, last cycle's new and matched, or why it is off).
- `data/ATTRIBUTION.txt` is written next to `leads.csv` (OpenStreetMap ODbL, Foursquare Apache 2.0, Overture CDLA Permissive 2.0, GeoNames CC BY).

### Changed
- **Excel history** (Sahil: "always add date time then space then fill, then again date time when I resume"): every export also adds to `data/leads.xlsx`, a bold column header once, then for each Start its date and time, a blank row, and the leads that run added (each lead once; a new listing of a contact already in the sheet is skipped). Text that looks like a formula stays text. While the file is open in Excel, the sheet update waits for the next export. `leads.csv` stays the full list. New `xlsx_batch` column; `openpyxl` added to `requirements.txt`.
- **More owner names** (Sahil: "I want to find every owner name possible"). Re-reading the 117 already-read websites on a copy of the database found **29 names, up from 6**. What the missed pages said, and the fix for each:
  - "Tara Lee Crooks Managing Partner & CEO" (title on its own line, no comma): a plain space before a capitalized senior title now counts (Owner, Founder, CEO, Managing Director, Managing Partner, Principal, Broker of Record); a bare "Director" or lower-case prose ("overseas owner") still does not.
  - "Brendan Powell - Broker of Record/Owner · Melanie Piche - Broker/Owner": "…/Owner" reads as Owner, and two owners or founders are both kept ("A & B"); three or more stay ambiguous.
  - "Dr Zeinab Taie" on the site of "Dentist Zeinab Taie (Dubai - Marina)": a doctor the site names whose name is in the business name is the owner (source "Business name").
  - Heading words before a name ("Staff Calum White", "Directors Anita Mehra") are dropped; branch and badge text ("Business Bay Branch", "Best Seller") is never a person.
- **UK owner names from Companies House** (`pipeline/companies_house.py`, free key, no card): one active company per lead or none, then its owners (over 25%) or the directors of a company with at most two. Tested against canned answers only; it has **not run against the live register yet** (needs Sahil's `COMPANIES_HOUSE_KEY`). New `ch_checked_at`, `ch_company` columns.
- **Owner names never get worse:** every writer (website reading, Companies House, LinkedIn) replaces a stored name only with one from an equally good or better source (`OWNER_RANK`).
- **Live feed shows every source** (Sahil: "add other sources to live feed"): new OpenStreetMap, Foursquare and Overture leads join the Google places, newest first, each labeled with its source. A lead Google found first shows once, from the Google file. Clear hides both kinds.
- **Live feed:** an Owner column (name and where it came from), and a **Clear** button that hides the places already shown; new places keep appearing, the next run shows in full, and no lead is deleted. The status cell "LinkedIn names (Brave)" is now "Owner names": the total, with Companies House and LinkedIn status under it.
- **Stop after N new leads:** an optional box next to Start. The run stops after the step that reaches N (Google, then the other sources, then website reading) and puts exactly N into its Excel block; any extra leads stay in the database for the next block.
- **Dashboard on narrow windows:** below about 840 px the whole page scrolled sideways, because the single-column layout could not shrink below the live-feed table's 820 px. The column now shrinks and the table scrolls inside its own card (checked at 732 px and on a 375 px phone).
- **Matching:** a shared website never joins two places whose phones differ (one hotel brand, one booking platform). WhatsApp, Telegram, Snapchat, short links (bit.ly, goo.gl, g.co, share.google), Google Sites and booking or listing sites (Talabat, Zomato, ChatFood, SevenRooms, Fresha, auto.ae, Parkopedia, Wikipedia) no longer count as a business's website, for matching or for crawling.
- **Sources column** lists every source that found a lead (`google_maps osm`).
- **LinkedIn box** says Brave's API needs a card, instead of only "add BRAVE_API_KEY".
- **Sector dropdown.** In "What to scrape", the country comes first; once one is chosen, a **Sectors** dropdown appears with the general sectors from `sectors.txt` (edit that file to change the list). Picking one adds it as a removable chip and greys it out in the list; "Or type your own" still takes any sector. Every sector is still searched in every location.

### Fixed

- Google searches kept only places inside the village's own 4 km box (`-grid-strict`), so a village with no carpenter of its own gave 0 leads even when Google returned dozens nearby (Abbey Dore: 91 jobs, 0 kept). The box filter is off; instead only places whose address is in the searched country are kept (Sahil: "country specific leads only"). A place with no address (a service-area tradesperson) is judged by its phone's country; with neither, it is dropped. The scraper already visited those places, so Google traffic is unchanged.
- Google added almost nothing (0–1 places per search since 17:01): grid searches sent the bare keyword ("carpenter"), and from an Indian IP Google ignored the map position and answered with carpenters in Pune; the box filter then dropped them all. Grid searches now name the place ("carpenter in Ailsworth, England"); a live test returned Peterborough-area carpenters. The 5 wasted village searches were queued again.
- `leads.xlsx` was only rewritten when there were new leads, so after a column change it kept the old columns. A sheet whose header differs from the current columns is now rebuilt.
- The run sat on "scraping" for an hour (carpenter, Acklington, 2026-11-20 18:46): gosom logged "scrapemate exited" but its headless Chromium never closed, so the container never ended. The runner now watches the log and stops the container a minute after that line, counting the run as finished.
- "Pressed Start, nothing happens": the country picked in the dropdown only became a target after pressing Add, so Save stored the sector with no locations. Save and Start now take a picked country as "All of <country>" (an already-listed country is not widened), Start saves unsaved changes first, and with no country or sector it says what is missing.
- Docker Desktop closed: every scrape failed ("scraper failed (exit 1)") and the run waited the full Google pause. The runner now checks Docker before each scrape, opens Docker Desktop and gives it 2 minutes, otherwise shows "Docker Desktop is not running" and retries in a minute, with no pause and no resume used up.
- The dashboard froze and Start stopped working at 88,000 leads. Two causes, measured on a copy: adding a run's leads to `leads.xlsx` reopened and resaved the whole workbook (over 10 minutes for 80,000 rows), and every 3-second page poll recomputed all counts, including one database query per town (13,600 for five countries), so polls piled up while the loop held Python busy. Now the sheet is rebuilt from the database in one streaming pass (30 s for 80,000 rows; a deleted sheet comes back whole), the counts use one query for all towns, and the page gets a status copy refreshed every 2 s in the background. Measured during a full export: page requests 0.07 s typical, 0.15 s at worst (was 15 s, then no answer).
- "Dashboard server not reachable" during a whole-country run: one country-wide Foursquare load held the database's write lock for minutes, and every page poll failed with "database is locked". The database now runs in WAL mode, so the page reads the last saved data while the loop writes.
- The export kept only leads with a verified email or a mobile, so most open-data places (about 600 of the first 927 Foursquare leads) never reached the sheet. Sahil: "every lead from every source in one Excel file". Every lead is now exported, best first, one row per business.
- "Stop after 5" while `leads.xlsx` was open in Excel said "Stopped after 0 new leads". It now says the sheet is open in Excel and that the new leads are kept for the next Start.

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
