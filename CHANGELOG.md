# Changelog

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
