# Lead Watch: Google Maps → verified leads

A dashboard with Start/Stop that scrapes Google Maps 24/7 for the sectors and locations you pick, reads each business's own website for contacts and owner names, verifies emails, and exports deduplicated leads to `data/leads.csv`.

Each cycle it:

1. Picks the next location that still has sectors to search.
2. Scrapes it with [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) in Docker. A location up to 2,500 km² (a city or district) is searched as a **grid** of 4 km cells, which finds far more than the ~120 places Google returns for one search. Bigger areas (whole countries) fall back to one plain search per sector.
3. Checks whether Google is blocking the IP, and backs off if so (see below).
4. Reads up to 300 business websites politely: robots.txt first, one page at a time per site, 1–2 s apart, at most 4 pages each (home, contact, about, team).
5. Optionally looks up missing owner names through Brave Search (see LinkedIn below).
6. Exports every lead with a verified email or a mobile number, best leads first.

## Dedupe (always on)

- **Searches:** a sector × location runs once, ever. Grid and plain searches are tracked separately.
- **Places:** stored by Google place id, so a business found by two searches is saved once.
- **Websites:** each site is read once, ever. Leads that share a website (a chain) share one read.
- **Contacts:** in the export, a repeated email or phone (chains, duplicate listings) appears once.

## Export columns (20)

Business Name, Owner Name, Owner Source, Address/Location, Postcode, City, Region, Business Sector, Category, Phone, Email, WhatsApp, Website, Total Google Reviews, Rating, Short Brief, Date Added, Authenticity Verdict, Social Links, Sources.

- **Owner Name** is best effort. **Owner Source** says where it came from, best first: Website structured data, Website team page, Business name ("Dr. …"), Website email link (a named mailto link), LinkedIn search result, Email address (guess), or Not found. Company-looking names ("SMILE DENTAL LTD") are never used as owners.
- **Phone** and **WhatsApp** use each country's standard grouping with the country code (`+971 4 566 2262`, `+44 20 7946 0958`, `+1 416-555-0199`), however Google wrote them. A number that can't be parsed is left as it was.
- **WhatsApp** is the mobile number. It is likely on WhatsApp but not checked.
- **Website** is empty when the Maps "website" is really a Facebook, Instagram or directory page; that link moves to **Social Links**, with the Instagram, Facebook, LinkedIn and other profiles found on the site.
- **Sources** says which source found the lead (`google_maps` for now).
- **Postcode / City** are empty where Google has none (for example the UAE). City then falls back to the searched location.
- **Authenticity Verdict** is the share of signals that apply to the lead: owner-claimed listing and 10+ reviews (Google leads), website, verified email, valid phone, and "site matches listing" (the website shows the listing's phone or name; counted only when the site was read). Verified at two thirds or more, Likely genuine at one third, otherwise Weak. Closed businesses are labeled Closed. The signals found are listed.

## Run it

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python pipeline\dashboard.py
```

Open http://localhost:8765, set sectors and locations, press **Save targets**, then **Start**. Docker must be running. The **Run & health** panel shows the current location, grid cell progress, Google's status, websites read and LinkedIn usage.

Knobs (environment variables):

| Variable | Default | What it does |
|---|---|---|
| `CONCURRENCY` | 2 | Parallel browser tabs in the scraper |
| `DEPTH` | 10 | How far each search scrolls (~120 places) |
| `GRID_CELL_KM` | 4 | Grid cell size; smaller finds more but takes longer |
| `PAUSE_MIN` | 30 | Rest between cycles (doubles while Google blocks) |
| `ENRICH_PER_CYCLE` | 300 | Websites read per cycle |
| `LINKEDIN_PER_CYCLE` | 30 | Brave searches per cycle |
| `BRAVE_API_KEY` | (none) | Turns on LinkedIn name lookups |
| `BRAVE_MONTHLY_CAP` | 1000 | Hard cap on Brave searches per month |
| `SMTP_CHECK` | off | `1` adds an SMTP mailbox check |

`blocked_domains.txt` lists website domains that are never read (government and health-service sites by default: `.gov`, `.gov.uk`, `.nhs.uk`, …). Add one suffix per line.

## When Google blocks

A run counts as blocked when most searches fail, when Google shows its "unusual traffic" page, or when it finds nothing without every search finishing cleanly. (Searches that all finish cleanly and find nothing just mean an empty area.) Each block doubles the pause (30 → 60 → 120 → 240 min) and a clean run resets it. After four blocks in a row the loop **stops itself** and the dashboard says "Google is blocking this IP. Stopped to protect it." Wait a day or move to another IP, then press Start. An interrupted run (Stop, crash, reboot, block) resumes where it left off rather than starting over. Website reading and LinkedIn lookups keep going while Google blocks, because they use other hosts.

If anything else fails (for example `leads.csv` is open in Excel), the loop writes the error to `data/runner.log`, shows it on the dashboard, and tries again after the pause.

## LinkedIn names (optional, via Brave Search)

Lead Watch never opens LinkedIn. For leads with no better owner name once their website has been read, it searches Brave for `"Business Name" City site:linkedin.com` and reads the result titles ("Sara Ali - Founder - Smile Clinic | LinkedIn"). A profile counts only if the result mentions the business. Each lead is searched once.

To turn it on (a human step):

1. Create a Brave Search API key at https://api-dashboard.search.brave.com/ (the free monthly credit covers about 1,000 searches).
2. In the Brave dashboard, set a **spending limit**, so you can never be charged beyond the credit.
3. Set the key in your environment, never in a file: `$env:BRAVE_API_KEY = "..."`, then start the dashboard.

Lead Watch also stops at `BRAVE_MONTHLY_CAP`, on Brave's "limit reached" responses, and when Brave rejects the key.

**Personal data:** owner names and profile links are personal data under GDPR and similar laws. Keep a lawful basis for holding them (usually legitimate interest, documented), tell people where you got their details when you first contact them, and delete them on request.

## Run 24/7 for free (Oracle Cloud Always Free, Ubuntu)

```bash
sudo apt update && sudo apt install -y docker.io python3-venv
sudo usermod -aG docker ubuntu && newgrp docker
# copy this folder to /home/ubuntu/lead-pipeline, then:
cd ~/lead-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deploy/lead-pipeline.service /etc/systemd/system/
sudo systemctl enable --now lead-pipeline
```

The dashboard listens on localhost only, because the Start/Stop buttons must not be public. To view it from your PC, use an SSH tunnel: `ssh -L 8765:localhost:8765 ubuntu@<vm-ip>`, then open http://localhost:8765. If the loop was running before a reboot, it resumes by itself.

## Staying safe

- **Google:** keep `CONCURRENCY` at 1–2 and the pause in place; the back-off above handles blocks. Never solve or bypass CAPTCHAs.
- **Websites:** Lead Watch identifies itself (`LeadWatch/0.2 … respects robots.txt`), obeys robots.txt and Crawl-delay, and skips any site that shows a bot check. It never tries to pass one. A website that redirects to Facebook, LinkedIn or a directory is not followed there; the link is kept under Social Links. A site asking for more than 30 s between pages gets its homepage read only.
- **Email:** most clouds block port 25, so SMTP checks usually fall back to `mx_ok` (the domain receives mail; the mailbox itself is not proven).
- **Law:** scraping may breach Google's terms, and contacting leads is regulated (GDPR, PECR, CAN-SPAM, CASL). WhatsApp bans accounts that send unsolicited bulk messages.

## Tests

```powershell
cd pipeline
foreach ($t in "process","grid","dashboard","backoff","contacts","enrich","linkedin") { ..\.venv\Scripts\python "test_$t.py" }
```
