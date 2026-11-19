# Changelog

## 2026-11-20: Phase A — grid search, resume, website reading, back-off, LinkedIn via Brave

### Added
- **Grid search.** Locations up to 2,500 km² are geocoded once (OpenStreetMap Nominatim, cached) and searched as a grid of 4 km cells (`GRID_CELL_KM`), getting past Google's ~120-places-per-search limit. Bigger areas use plain searches.
- **Resume.** A run interrupted by Stop, a crash, a reboot or a block continues where it left off (`-resume`), instead of starting over. A run that crashes three times is dropped and its searches stay queued.
- **Block back-off and auto-stop.** Empty runs, mostly failed searches or Google's "unusual traffic" page count as a block. The pause doubles up to 4 hours, and four blocks in a row stop the loop with a clear message.
- **Polite website reading.** Up to 300 sites per cycle (`ENRICH_PER_CYCLE`), each read once: robots.txt and Crawl-delay obeyed, 1–2 s between pages, at most 4 pages (home plus contact/about/team, with a sitemap fallback), 1 MB per page, a bounded number of retries, and any bot-check page skipped. It finds emails (including "name [at] domain" forms and named mailto links), phones, social profiles and owner names (from structured data or team pages).
- **LinkedIn names via Brave Search** (optional, `BRAVE_API_KEY`). LinkedIn itself is never opened. Capped per month (`BRAVE_MONTHLY_CAP`, default 1000) and per cycle (`LINKEDIN_PER_CYCLE`, 30), and it stops on Brave's limit responses.
- `blocked_domains.txt`: website domains never read (government and health-service sites by default).
- **Export columns 19–20:** Social Links and Sources.
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
