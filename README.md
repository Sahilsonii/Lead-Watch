<div align="center">

# 🛰️ Lead Watch

**Google Maps + OpenStreetMap + Overture Maps + Foursquare → verified, deduplicated business leads, 24/7.**

Pick sectors and locations, press **Start**, and Lead Watch scrapes Google Maps, adds free open-data places from OpenStreetMap and Overture Maps (and Foursquare, once you sign up), reads each business's own website for contacts and owner names, verifies emails, and exports clean leads to `data/leads.csv` and `data/leads.xlsx`.

![Python](https://img.shields.io/badge/python-3-3776AB?logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/needs-Docker-2496ED?logo=docker&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-4-brightgreen)
![Dashboard](https://img.shields.io/badge/dashboard-localhost%3A8765-orange)
![License: MIT](https://img.shields.io/badge/license-MIT-blue)

[Quick start](#-quick-start) · [How it works](#-how-it-works) · [Dashboard](#-the-dashboard) · [Export](#-what-you-get) · [Settings](#%EF%B8%8F-settings) · [24/7 hosting](#%EF%B8%8F-run-it-247-for-free) · [Staying safe](#%EF%B8%8F-staying-safe)

</div>

---

## 🚀 Quick start

You need Python 3 and Docker (running).

```powershell
git clone https://github.com/Sahilsonii/Lead-Watch.git; cd Lead-Watch
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python pipeline\dashboard.py
```

Open **http://localhost:8765**, pick sectors and locations, press **Save targets**, then **Start**. To try a small run first, type a number in **Stop after … leads** (for example 5) before pressing Start: the run stops once that many new leads are in.

<details>
<summary>🐧 On macOS / Linux</summary>

```bash
git clone https://github.com/Sahilsonii/Lead-Watch.git && cd Lead-Watch
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python pipeline/dashboard.py
```

</details>

---

## 🔄 How it works

Every cycle runs this loop until you press **Stop**:

```mermaid
flowchart LR
    A["📍 Pick next location<br/>with sectors left"] --> B["🗺️ Scrape Google Maps<br/>gosom scraper in Docker<br/>4 km grid for cities"]
    B --> C{"🚦 Google<br/>blocking?"}
    C -- yes --> P["⏸️ Back off<br/>30, 60, 120, 240 min<br/>stop at block score 4"]
    C -- no --> S["🧭 Add places from<br/>OpenStreetMap, Overture,<br/>Foursquare, matched to<br/>existing leads"]
    P --> S
    S --> D["🌐 Read business websites<br/>robots.txt first, max 4 pages"]
    D --> E["🔎 Owner names via<br/>Companies House (UK),<br/>Brave Search, optional"]
    E --> F["✅ Verify, dedupe, score<br/>export data/leads.csv"]
    F --> R["😴 Rest PAUSE_MIN"]
    R --> A
```

<details>
<summary><b>Step by step</b></summary>

1. **Pick** the next location that still has sectors to search. Single cities always come first, then any [whole country](#-whole-country-search), place by place.
2. **Scrape** it with [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) in Docker. A location up to 2,500 km² (a city or district) is searched as a **grid** of 4 km cells, which finds far more than the ~120 places Google returns for one search. Bigger areas (states, whole countries) fall back to one plain search per sector.
3. **Check** whether Google is blocking the IP, and back off if so ([details](#-when-google-blocks)).
4. **Add** places from OpenStreetMap, Foursquare and Overture Maps for the same sectors and locations ([other sources](#-other-sources)). This runs even while Google is blocking, because it uses other hosts.
5. **Read** up to 300 business websites politely: robots.txt first, one page at a time per site, 1–2 s apart, at most 4 pages each (home, contact, about, team).
6. **Look up** missing owner names: UK businesses in Companies House ([UK owner names](#-uk-owner-names-companies-house)), then Brave Search if you turned it on ([LinkedIn names](#-linkedin-names-optional)).
7. **Export** every lead from every source to `leads.csv` and `leads.xlsx`, best leads first (verified email and mobile at the top), one row per business.

</details>

### ♻️ Dedupe is always on

| Level | Rule |
|---|---|
| **Searches** | A sector × location runs once, ever. Grid and plain searches are tracked separately. |
| **Places** | Stored by Google place id, so a business found by two searches is saved once. A place from OpenStreetMap, Foursquare or Overture joins an existing lead when the phone matches or the website domain matches (each within 500 m, so chain branches stay apart), or when the name is 85% alike within 150 m. A shared phone never joins two places with different websites (one building, one reception, two clinics), and a shared website never joins two places with different phones (hotels of one brand, restaurants on one booking site). WhatsApp, Telegram, short links, Google Sites and booking platforms never count as a website. It only fills empty fields; Google's values win. |
| **Websites** | Each site is read once, ever. Leads that share a website (a chain) share one read. |
| **Contacts** | In the export, a repeated email or phone (chains, duplicate listings) appears once. |

---

## 🖥️ The dashboard

One local page at `http://localhost:8765` with:

- **What to scrape:** pick a country (and cities, if you like), then choose sectors from the dropdown that appears, or type your own. Press **Save targets**. The dropdown's list lives in `sectors.txt`.

### 🌍 Whole-country search

Pick a country, leave cities empty and press **Add**: the chip reads **All of India**. Lead Watch then works through the whole country by itself, one place per cycle, **smallest areas first**:

| Order | Level | What it is | How it is searched |
|---|---|---|---|
| 1 | Villages | 1,000–15,000 people | grid over a 4 km box around the village |
| 2 | Towns | 15,000–100,000 people | grid over an 8 km box around the town |
| 3 | Cities | 100,000+ people | grid over the city's real outline (OpenStreetMap) |
| 4 | States | each state or region | plain search per sector |
| 5 | Country | the whole country | plain search per sector |

Inside each level the least populated place goes first. Cities you add yourself always run before any country's places, and a place you already listed is searched once. Picking a country shows the size of the job (for India: 7,213 places, so at least 151 days at one place per 30-minute cycle), and the run panel shows where the sweep is ("Villages · 12 of 3,433 · Uttar Pradesh").

Place data comes from [GeoNames](https://www.geonames.org/) (CC BY 4.0), downloaded once (about 10 MB) into `data/geo/` the first time you pick a country.
- **Start / Stop:** the choice survives a reboot. If the loop was running, it resumes by itself.
- **Run & health:** the current location, grid cell progress, Google's status, websites read and LinkedIn usage.
- **Light / dark** toggle that remembers your choice.

> [!NOTE]
> The dashboard listens on localhost only, because the Start/Stop buttons must not be public.

---

## 📦 What you get

`data/leads.csv` (every lead, rewritten each cycle) and `data/leads.xlsx` (a running history: each time you press Start, the date and time, a blank row, then the leads that run added, so each lead appears once) with 20 columns:

> Business Name · Owner Name · Owner Source · Address/Location · Postcode · City · Region · Country · Business Sector · Category · Phone · Email · WhatsApp · Best Way to Reach · Website · Total Google Reviews · Rating · Short Brief · Date Added · Authenticity Verdict · Social Links · Sources

<details>
<summary><b>What each tricky column means</b></summary>

- **Owner Name** is best effort. **Owner Source** says where it came from, best first: Website structured data, Website team page, Companies House owner (UK, over 25% of the company), Business name ("Dr. …", or a doctor the site names who is also in the business name), Companies House director (UK, companies with at most 2 directors), Website email link (a named mailto link), LinkedIn search result, Email address (guess), or Not found. A weaker source never replaces a stronger one. Two owners or founders listed side by side are both kept ("Brendan Powell & Melanie Piche"). Company-looking names ("SMILE DENTAL LTD") are never used as owners.
- **Phone** and **WhatsApp** use each country's standard grouping with the country code (`+971 4 566 2262`, `+44 20 7946 0958`, `+1 416-555-0199`), however Google wrote them. A number that can't be parsed is left as it was.
- **Country** is the full country name ("United Kingdom"), from the lead's stored country code.
- **Best Way to Reach** names the strongest channel the lead has, in this order: WhatsApp (a mobile number), Email (a verified email), Call (any phone), Website (its own site, for the contact form), Social media. It is blank when the lead has none of these.
- **WhatsApp** is the mobile number. It is likely on WhatsApp but not checked. It keeps its spaces for reading; strip them and the `+` for a `wa.me/` link: `+971 50 123 4567` → `wa.me/971501234567`.
- **Website** is empty when the Maps "website" is really a Facebook, Instagram or directory page; that link moves to **Social Links**, with the Instagram, Facebook, LinkedIn and other profiles found on the site.
- **Sources** lists every source that found the lead: `google_maps`, `osm`, `foursquare`, `overture` (for example `google_maps overture`). Leads found only through the other sources have no Google reviews or rating; their verdict uses the other signals.
- **Postcode / City** are empty where Google has none (for example the UAE). City then falls back to the searched location.

</details>

<details>
<summary><b>How the Authenticity Verdict is scored</b></summary>

The verdict is the share of these signals that apply to the lead:

| Signal | Applies to |
|---|---|
| Owner-claimed listing | Google leads |
| 10+ reviews | Google leads |
| Has a website | all |
| Verified email | all |
| Valid phone | all |
| Site matches listing (shows the listing's phone or name) | only when the site was read |

| Share of signals | Verdict |
|---|---|
| ≥ ⅔ | ✅ Verified |
| ≥ ⅓ | 🟡 Likely genuine |
| less | 🔴 Weak |
| business closed | ⚫ Closed |

The signals found are listed next to the verdict.

</details>

---

## ⚙️ Settings

Set these as environment variables before starting the dashboard.

<details>
<summary><b>All settings</b></summary>

| Variable | Default | What it does |
|---|---|---|
| `CONCURRENCY` | 2 | Parallel browser tabs in the scraper |
| `DEPTH` | 10 | How far each search scrolls (~120 places) |
| `GRID_CELL_KM` | 4 | Grid cell size; smaller finds more but takes longer |
| `PAUSE_MIN` | 30 | Rest between cycles (doubles while Google blocks) |
| `ENRICH_PER_CYCLE` | 300 | Websites read per cycle |
| `LINKEDIN_PER_CYCLE` | 30 | Brave searches per cycle |
| `OSM_PER_CYCLE` | 20 | OpenStreetMap (Overpass) queries per cycle |
| `OVERPASS_URL` | overpass-api.de | Another Overpass server, if that one is busy |
| `HF_TOKEN` | (none) | Turns on Foursquare (see [other sources](#-other-sources)) |
| `FSQ_PER_CYCLE` | 20 | Foursquare reads per cycle |
| `OVERTURE_PER_CYCLE` | 20 | Overture Maps reads per cycle |
| `COMPANIES_HOUSE_KEY` | (none) | Turns on UK owner names from Companies House (free key, no card) |
| `CH_PER_CYCLE` | 100 | UK leads looked up in Companies House per cycle |
| `BRAVE_API_KEY` | (none) | Turns on LinkedIn name lookups (Brave asks for a card) |
| `BRAVE_MONTHLY_CAP` | 1000 | Hard cap on Brave searches per month |
| `SMTP_CHECK` | off | `1` adds an SMTP mailbox check |
| `LEADS_XLSX` | `data/leads.xlsx` | Where the Excel file is written, such as a [shared OneDrive folder](#-share-the-excel-file-onedrive) |

`blocked_domains.txt` lists website domains that are never read (government and health-service sites by default: `.gov`, `.gov.uk`, `.nhs.uk`, …). Add one suffix per line.

</details>

### 🚧 When Google blocks

<details>
<summary><b>How blocking is detected and handled</b></summary>

A run counts as blocked when most searches fail, when Google shows its "unusual traffic" page, or when it finds nothing without every search finishing cleanly. (Searches that all finish cleanly and find nothing just mean an empty area.)

Each blocked run adds 1 to a **block score**; each clean run takes 0.5 off (never below 0). So one lucky run between blocks does not reset the danger.

| After a run | Pause before next cycle |
|---|---|
| Clean run | 30 min (back to normal) |
| 1st block | 60 min |
| 2nd block | 120 min |
| 3rd block and later | 240 min (the cap) |
| Block score reaches 4 | **Loop stops itself** |

At a score of 4 the dashboard says "Google is blocking this IP. Stopped to protect it." Wait a day or move to another IP, then press Start. An interrupted run (Stop, crash, reboot, block) resumes where it left off rather than starting over. OpenStreetMap, Overture, Foursquare, website reading and LinkedIn lookups keep going while Google blocks, because they use other hosts.

If anything else fails (for example `leads.csv` is open in Excel), the loop writes the error to `data/runner.log`, shows it on the dashboard, and tries again after the pause.

</details>

### 🧭 Other sources

<details>
<summary><b>OpenStreetMap, Overture Maps, Foursquare, and why not Tripadvisor</b></summary>

All three are free and need no card. They search the same sectors and locations as Google, mapped to their own categories in [`categories.json`](categories.json) (edit it to change what a sector means there). Only places with a phone, website or email are kept, and each one is [matched](#%EF%B8%8F-dedupe-is-always-on) to the leads already saved.

**OpenStreetMap** (on by default, no account). Queried through the public Overpass API, one query per sector and location, 2 s apart. A single city uses its geocoded area; a whole country is one query, split into its towns and cities when Overpass says it is too big. When Overpass is busy, OSM waits for the next cycle. Data © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright): a database you build from it and share must stay under the ODbL.

**Overture Maps** (on by default, no account). Overture's open places data (CDLA Permissive 2.0, mostly from Meta and Microsoft) is read with DuckDB straight from its public bucket: the first read of a country copies its open places into `data/overture/<CC>.parquet` (the UAE: about 30 s and 20 MB), and every query after that is local. Places Overture itself rates below 0.5 confidence are skipped. `python pipeline/overture.py check AE` lists category ids in `categories.json` that match no place. In a live test, Dubai dentists gave 661 new leads (472 with an email) on top of 111 from Google.

**Foursquare Open Source Places** (Apache 2.0, off until you sign up). The data sits on Hugging Face behind a one-time form:

- [ ] Sign in at https://huggingface.co and open https://huggingface.co/datasets/foursquare/fsq-os-places. Fill in the short form and press **Agree**. (By agreeing, Foursquare may name your organization as a user of the data.)
- [ ] Create a **read** token at https://huggingface.co/settings/tokens.
- [ ] Set it in your environment, never in a file: `$env:HF_TOKEN = "..."`, then start the dashboard.
- [ ] Optional: `python pipeline/fsq.py check AE` lists category labels in `categories.json` that match no place, so they can be corrected.

The first read of a country copies that country's open places (out of an ~11.6 GB release) into `data/fsq/<CC>.parquet`, once; every query after that is local. This needs `duckdb` (in `requirements.txt`).

**Tripadvisor is not used.** A single polite request on 2026-11-20 got a 403 with a DataDome bot check, and Lead Watch never tries to pass bot checks (the same reason Yelp and Yellow Pages are out).

`data/ATTRIBUTION.txt` is written next to `leads.csv` with the credits these licenses require.

</details>

### 🇬🇧 UK owner names (Companies House)

<details>
<summary><b>How it works and how to turn it on</b></summary>

Companies House is the UK's official company register. Its API is free and needs no card. For each UK lead with no better owner name, Lead Watch searches the register by business name and takes the **one active company** whose legal name fits ("Marsh & Parsons London Bridge Estate Agents" → MARSH & PARSONS LIMITED). When two companies fit, or the name is a single word with no matching postcode, the lead gets no name rather than a stranger's. The company number is kept in the `ch_company` column so any match can be checked.

From that company it takes the people holding over 25% of it (Owner), or else its directors when there are at most two (Director); bigger firms' directors are not the local owner, so they are skipped. Each lead is looked up once, at most 3 calls, spaced to stay under the register's 600 calls per 5 minutes.

To turn it on:

- [ ] Create a free account at https://developer.company-information.service.gov.uk/ and register an application with a **REST API key**.
- [ ] Save the key as a user environment variable, never in a file: `[Environment]::SetEnvironmentVariable("COMPANIES_HOUSE_KEY", "...", "User")`, then restart the dashboard.
- [ ] Check one business by hand: `python pipeline/companies_house.py "Business Name"` lists what the register has and which company Lead Watch would pick.

</details>

### 🔎 LinkedIn names (optional)

<details>
<summary><b>How it works and how to turn it on</b></summary>

**Not free:** Brave's Search API asks for a card on file, even for its monthly free credit, so this stays off unless you choose it. Everything else in Lead Watch is free.

Lead Watch never opens LinkedIn. For leads with no better owner name once their website has been read, it searches Brave for `"Business Name" City site:linkedin.com` and reads the result titles ("Sara Ali - Founder - Smile Clinic | LinkedIn"). A profile counts only if the result mentions the business. Each lead is searched once.

To turn it on:

- [ ] Create a Brave Search API key at https://api-dashboard.search.brave.com/ (the free monthly credit covers about 1,000 searches).
- [ ] In the Brave dashboard, set a **spending limit**, so you can never be charged beyond the credit.
- [ ] Set the key in your environment, never in a file: `$env:BRAVE_API_KEY = "..."`, then start the dashboard.

Lead Watch also stops at `BRAVE_MONTHLY_CAP`, on Brave's "limit reached" responses, and when Brave rejects the key.

</details>

### 📗 Share the Excel file (OneDrive)

<details>
<summary><b>How to give the team the Excel file online</b></summary>

Point `LEADS_XLSX` at a file inside your OneDrive folder. Lead Watch writes the Excel file there after each export, and OneDrive puts it online, where the team opens it in Excel for the web or the Excel app. No extra account or key.

- [ ] Make a folder in OneDrive, such as `Lead Watch`, and share it with the team (right-click → **Share**), view only.
- [ ] Save the location as a user environment variable, then restart the dashboard:

```powershell
[Environment]::SetEnvironmentVariable("LEADS_XLSX", "$env:OneDrive\Lead Watch\leads.xlsx", "User")
```

The file is rebuilt from the database on every export, so **changes made in it are overwritten**: share it view only, and keep call notes in a separate file. While the file is open in the Excel app on this PC, Windows locks it and the export waits (the dashboard says so); a teammate viewing it online does not block it. `leads.csv` and the database stay in `data/`.

</details>

> [!IMPORTANT]
> Owner names and profile links are personal data under GDPR and similar laws. Keep a lawful basis for holding them (usually legitimate interest, documented), tell people where you got their details when you first contact them, and delete them on request.

---

## ☁️ Run it 24/7 for free

<details>
<summary><b>Oracle Cloud Always Free (Ubuntu) with systemd</b></summary>

```bash
sudo apt update && sudo apt install -y docker.io python3-venv
sudo usermod -aG docker ubuntu && newgrp docker
# copy this folder to /home/ubuntu/lead-pipeline, then:
cd ~/lead-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deploy/lead-pipeline.service /etc/systemd/system/
sudo systemctl enable --now lead-pipeline
```

To view the dashboard from your PC, use an SSH tunnel, then open http://localhost:8765:

```bash
ssh -L 8765:localhost:8765 ubuntu@<vm-ip>
```

If the loop was running before a reboot, it resumes by itself.

</details>

---

## 🛡️ Staying safe

- **Google:** keep `CONCURRENCY` at 1–2 and the pause in place; the back-off handles blocks. Never solve or bypass CAPTCHAs.
- **Websites:** Lead Watch identifies itself (`LeadWatch/0.2 … respects robots.txt`), obeys robots.txt and Crawl-delay, and skips any site that shows a bot check. It never tries to pass one. A website that redirects to Facebook, LinkedIn or a directory is not followed there; the link is kept under Social Links. A site asking for more than 30 s between pages gets its homepage read only.
- **Email:** most clouds block port 25, so SMTP checks usually fall back to `mx_ok` (the domain receives mail; the mailbox itself is not proven).

> [!WARNING]
> Scraping may breach Google's terms, and contacting leads is regulated (GDPR, PECR, CAN-SPAM, CASL). WhatsApp bans accounts that send unsolicited bulk messages. You are responsible for how you use this tool and the data it collects.

---

## 🧪 Tests

Each test is a plain Python script with asserts, no test runner needed.

```powershell
cd pipeline
foreach ($t in "process","merge","osm","fsq","overture","grid","geo","dashboard","backoff","contacts","enrich","companies_house","linkedin") { ..\.venv\Scripts\python "test_$t.py" }
```

<details>
<summary><b>Project layout</b></summary>

```text
pipeline/
  dashboard.py / .html   local dashboard, Start/Stop, the run loop
  grid.py                plans each run as a grid of map cells (or plain searches)
  geo.py                 every village, town, city and state of a country (GeoNames)
  backoff.py             block detection and pause doubling
  enrich.py              polite website reader (robots.txt, one request at a time)
  contacts.py            email, phone, owner name and social link extraction
  osm.py                 OpenStreetMap places through the Overpass API
  fsq.py                 Foursquare Open Source Places (optional, read with DuckDB)
  overture.py            Overture Maps places (read with DuckDB from the public bucket)
  companies_house.py     UK owner names from the Companies House register (optional, free key)
  linkedin.py            optional owner names via Brave Search
  process.py             lead database: ingest, cross-source matching, verify emails, dedupe, CSV export
  test_*.py              one assert-based test script per module
deploy/lead-pipeline.service   systemd unit for 24/7 hosting
niches.txt, cities.txt         default sectors and locations
sectors.txt, categories.json   the sector dropdown, and what each sector means on OpenStreetMap, Foursquare and Overture
blocked_domains.txt            domains that are never read
data/                          leads.db, leads.csv, runner.log, geo/ (git-ignored)
```

</details>

See [CHANGELOG.md](CHANGELOG.md) for what changed. Released under the [MIT License](LICENSE). Place data © [GeoNames](https://www.geonames.org/) (CC BY 4.0), © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL), [Foursquare Open Source Places](https://huggingface.co/datasets/foursquare/fsq-os-places) (Apache 2.0), and [Overture Maps](https://overturemaps.org) (CDLA Permissive 2.0).
