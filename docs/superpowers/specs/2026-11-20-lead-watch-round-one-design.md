# Lead Watch, round one: squeeze Google Maps (A), then add more sources (B)

Date: 2026-11-20 · Status: approved by Sahil (2026-11-20)

## Intent

Lead Watch scrapes Google Maps 24/7 for the sectors and locations picked on its dashboard. It verifies emails, flags mobiles as likely WhatsApp, and exports deduplicated leads in Sahil's 18-column CSV. For now the leads are only **stored**: no outreach, CRM sync or sending. So "better" means three things:

1. **More businesses**: find the ones we miss today.
2. **More complete, accurate records**: owner names, real emails, social links, a stronger authenticity verdict.
3. **A loop that stays alive** when Google slows us down, without losing work.

## Goals

- Get past Google's ~120-results-per-search cap.
- Find owner/doctor names and more emails by reading each business's contact, about and team pages.
- Detect blocking and back off automatically, keeping everything found so far.
- (Phase B) Add Foursquare Open Places and OpenStreetMap as second sources that need no scraping, plus Tripadvisor (restaurants, hotels, attractions) by polite page reading, all merged into the same deduplicated lead store.

## Non-goals

- Outreach of any kind: sending email or WhatsApp, sequences, CRM export.
- WhatsApp number checking (removed on purpose, because of ban risk).
- Country-specific registries such as UK Companies House or DHA Sheryan. That is "direction C", a later round, per country.
- Paid sources or services: omkarcloud credits, Yelp, OpenCorporates paid tier, paid proxies. Proxy *support* may come later, but nothing that needs money now. The one exception is Brave's monthly free credit for A6, kept inside the credit by a hard cap.
- Replacing gosom/google-maps-scraper with another engine.
- Yelp, Trustpilot and Yellow Pages (US, UK yell.com, Canada). Each refused a plain request with 403 on 2026-11-20, so reaching them would need the bot evasion ruled out below. Reddit, GitHub and Crunchbase hold no local-business leads. Instagram/Facebook are reached only as links found on business websites.
- The Tripadvisor Content API. It licenses data for display on your own site, not for a stored lead list, and a third-party report says it sunsets on 2026-08-31.

## Anti-goals (failure even if it "works")

- **Inventing data.** A wrong owner name is worse than an empty one. Every guessed value keeps a source label.
- **Bypassing CAPTCHAs or bot checks.** On a block we slow down or stop.
- **Scraping LinkedIn or Facebook pages** (against their terms).
- **Copying AGPL code** from google-maps-scraper-kit. Re-implement the ideas only.
- **Breaking dedupe or the 18-column export.** Existing columns keep their meaning.
- Hammering small business websites. The crawl must stay polite.

## Constraints

- Free to run. Target host is an Oracle Cloud Always Free VM (ARM, Ubuntu). It must also run on Sahil's Windows PC with Docker Desktop.
- Stack stays as is: Python 3 (stdlib plus `dnspython` and `phonenumbers`), gosom in Docker (pinned `v1.19.0`), SQLite `data/leads.db`, the dashboard in `pipeline/dashboard.py` + `dashboard.html`.
- The dashboard listens on localhost only.
- Datacenter IPs get rate-limited by Google, so concurrency stays low (default 2).
- Outbound port 25 is usually blocked on clouds, so SMTP mailbox checks stay optional.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A** | Get more out of Google Maps: grid search, multi-page website crawl, block back-off | Small to medium changes, works in every country, no new accounts. Still one main source. |
| **B** | Add second sources: Foursquare Open Places and OpenStreetMap (no scraping), plus Tripadvisor (polite page reading, added 2026-11-20) | Fills gaps. The two open datasets never get IP-blocked; Tripadvisor can, so it gets its own back-off. Needs cross-source matching, a sector-to-category mapping and a Foursquare sign-up. UAE coverage unknown for Foursquare/OSM. |
| C | Country registries for owner names | Most accurate names, but fuzzy matching and one job per country. |

**Picked: A first, then B** (Sahil, 2026-11-20). A improves volume and owner names everywhere at no cost. B adds coverage once A is solid. C is deferred.

## Design: Phase A

### A1. Grid search per city (beat the 120 cap)

- gosom v1.19.0 supports `-grid-bbox "minLat,minLon,maxLat,maxLon"` with `-grid-cell <km>`, plus `-zoom` and optional `-grid-strict`. It runs one search per grid cell. This is verified in its v1.19.0 README.
- A bounding box applies to a whole run, so **a run covers one location**: all its pending sectors as queries (e.g. "dentist in Dubai", "real estate agency in Dubai") over one bbox. This replaces today's "next 5 searches" batch.
- **Getting the bbox:** geocode each location once with OpenStreetMap Nominatim (free; max 1 request/s; identify with a generic app User-Agent, never personal details). Cache the result in a new `locations` table (`name, cc, bbox, cell_km`). Show the bbox on the dashboard so a bad geocode is visible.
- **Cell size:** default 4 km (my call, revised after Sahil chose maximum depth). Each cell can now return up to ~120 places, so bigger cells lose little and need about 4× fewer searches than 2 km cells: roughly 150 cells per sector for a 60×40 km city instead of ~600. Expect several hours per sector at concurrency 2, which suits a 24/7 loop. Lower it to 2 km for very dense sectors such as restaurants.
- **Country-only targets** ("whole UAE", no city) are too large for a grid. They keep plain searches, and the dashboard hint suggests adding cities.
- **Oversized geocodes** (my call): Nominatim may return a whole emirate or province for "Dubai" or "Riyadh". If a bbox is larger than 2,500 km², that location falls back to plain searches. The dashboard shows the bbox and its area so this is visible. Editing the bbox by hand is left to a later round.
- Use `-grid-strict` so places from neighboring areas don't pollute a location. Dedupe by place_id already absorbs overlaps between cells.
- **Grid searches are their own search key** (my call), e.g. `grid:dentist|Dubai`. Sector/location pairs already done as plain searches (dentist in Dubai, real estate agency in Dubai, dentist in London) get one grid pass. Dedupe stays always on where it matters: places are still stored once per place_id and contacts once in the export. A finished grid pass is done for good, like plain searches today.
- **Depth per search: maximum** (Sahil, 2026-11-20). Each search, plain or grid cell, scrolls to depth 10, up to about 120 places, instead of today's depth 1 (about 20). This trades speed and IP safety for volume, so A4's back-off is what keeps the loop alive. The value is one setting (`DEPTH`, default 10) so it can be lowered if blocks become constant.

### A2. Resume interrupted runs

- Use gosom `-resume` with the same results file when a run is restarted after Stop, a crash or a reboot. Today an interrupted batch re-runs from scratch.
- The runner records the in-progress run (results file, location, queries, bbox, cell size) in `data/state.json`. On the next Start it relaunches with `-resume` and identical options, as gosom requires.

### A3. Multi-page website crawl (emails, owner names, social links, authenticity)

A new step after ingest, `pipeline/enrich.py`. For each lead with a website that hasn't been enriched yet:

- **Pages:** fetch the homepage, then up to 3 more same-site pages: links whose URL or text matches contact, about, team, our-doctors, staff, leadership, founder. Each page has a 10 s timeout and a 1 MB size cap. Up to 8 sites are crawled at once, with one request at a time per site.
- **Politeness:** honor robots.txt (stdlib `urllib.robotparser`) and send a generic, honest User-Agent. Skip sites that refuse or time out, without retrying them in the same run.
- **Extract:**
  - Emails: `mailto:` links and text, through the existing junk filter and MX verification. The best email is still chosen by the existing rules.
  - Phones: for the authenticity check, and as a fallback if Maps had none.
  - Social links: Instagram, Facebook, LinkedIn, TikTok, YouTube, X. These are URLs read from the site; we never fetch the social sites themselves.
  - **Owner/decision-maker names**, in priority order:
    1. schema.org JSON-LD `Person` / `founder` / `employee` with a role.
    2. Team or about pages: a name next to Founder, Owner, CEO, Managing Director, Director, Principal, Partner, or "Dr." with a role.
    3. Existing fallbacks: "Dr. …" in the business name, then a personal-looking email (marked "guess").
  - **Owner Source** says which rule matched, e.g. "Website team page". Ambiguous matches are skipped rather than guessed (anti-goal: inventing data).
- **Authenticity:** a new signal, "site matches listing": the site shows the listing's phone (last 7 digits) or the business name. This feeds the existing Authenticity Verdict.
- **Verdict scoring (my call):** scored on the share of signals that *apply* to the lead, so adding a signal doesn't quietly make "Verified" easier. Verified = at least 2/3 of the applicable signals; Likely genuine = at least 1/3; Weak = less. Closed stays Closed. Google-only signals (owner-claimed, 10+ reviews) don't apply to leads found only through Phase B, so those leads can still reach Verified. Tests and the README are updated to match.
- **Re-crawl policy:** once per lead. Re-crawling stale leads is left to a later round.
- **Picking the extra pages** (ideas from crawl4ai and firecrawl, re-implemented, no code copied):
  - Resolve links against `<base href>` when present.
  - Keep same-site links only, and drop `mailto:`, `tel:` and `javascript:` links from page discovery (their values are still extracted).
  - Rank candidates by keyword match in the URL or link text (contact, about, team, doctors, staff, leadership, founder). Prefer shallow paths.
  - If the homepage yields fewer than 3 candidates, read `sitemap.xml` (including the `Sitemap:` line in robots.txt) for more. This catches pages only linked from JS menus.
- **JSON-LD:** parse every `<script type="application/ld+json">`, flatten `@graph` arrays, and tolerate invalid JSON by skipping it.
- **Politeness mechanics** (Colly's LimitRule shape, Scrapy's AutoThrottle idea):
  - Global `asyncio.Semaphore(8)`, plus one `Semaphore(1)` per site.
  - A 1–2 s randomized delay between pages of the same site, raised to the site's `Crawl-delay` if robots.txt sets one.
  - Retry only on 408, 429, 500, 502, 503, 504, 522, 524, at most twice, honoring `Retry-After`. Other errors skip the page.
  - Scrapy-style latency-based AutoThrottle is optional, only if fixed delays prove wrong in practice.
- **robots.txt handling:** fetch it with httpx (10 s timeout) and feed `urllib.robotparser.parse()`, because the stdlib `read()` has no timeout. Status rules: 404 = allow, 401/403 = disallow, 5xx or timeout = skip the site (RFC 9309's polite reading). Cache the rules per site for the run. Use Protego only if the stdlib parser misreads real files (wildcards).
- **Block/challenge pages** (crawl4ai's tiered `is_blocked` idea, re-implemented): if a site answers with a Cloudflare, Akamai, DataDome, PerimeterX or Incapsula challenge, an empty shell, or a 403/503 whose body confirms a block, mark the site `blocked` and skip it. Never try to pass the challenge, and never mine a challenge page for data. If any marker strings are copied verbatim from crawl4ai, keep its required attribution line in the README.
- **Deferred to round two:** a browser fallback for sites where plain HTTP finds no email or name (Crawlee's HTTP-first idea, no ML). It would use Playwright with images, fonts, media and stylesheets blocked through `page.route`, one page at a time, run only between gosom runs, and ideally connect over CDP to the Chromium already in gosom's image rather than downloading a second browser.
- **Libraries:** `httpx` for fetching and `selectolax` (or stdlib `html.parser`) for parsing. The builder picks the smallest that handles real-world HTML. No headless browser in round one, so JS-only sites will yield little. A browser fallback is a later round.
- **From EraSDR** (Sahil's own repo, reviewed 2026-11-20; the rules below are ported to Python from `src/lib/ai/lead-contact.ts`, `name-kind.ts`, `scrape/blocked-domains.ts` and `scrape/rank-urls.ts`):
  - **Named emails:** read `Jane Smith <jane@…>` and `[Jane Smith](mailto:jane@…)` so the name next to an email becomes an owner candidate (Owner Source = "Website email link").
  - **Obfuscated emails:** decode `jane [at] clinic [dot] com` and similar forms.
  - **More junk email hosts:** add sentry.io, wixpress.com, googleapis.com, gstatic.com, cloudfront.net, schema.org and w3.org to the junk filter (these come from site code, not people).
  - **Person vs. company names:** reject an "owner" that looks like a business (Ltd, LLC, Inc, Group, Services, Partners, all-caps words, or the business name itself), so "SMILE DENTAL LTD" never lands in Owner Name.
  - **Not the business's own site:** when the Maps "website" is Facebook, Instagram, LinkedIn, Yelp, TripAdvisor or a similar aggregator, don't crawl it. Store it under Social Links instead.
  - **Blocked domains:** a short editable list (default `.gov`, `.gov.uk`, `.nhs.uk`, `.mod.uk`, `.police.uk`) whose sites are never crawled.
  - **Extra contact paths:** add enquiry, get-in-touch, reach-us and book to the page keywords.

### A4. Block detection and back-off

- After each run, classify it as **ok**, **suspected block** or **error**:
  - Suspected block: zero places from a non-empty batch, more than half the jobs failed in `data/scraper.log`, or the log mentions "unusual traffic".
- On a suspected block: keep and ingest what was found, mark the run's queries *not done*, and double the pause (30 → 60 → 120 → 240 min cap). A clean run resets the pause to the default.
- Never solve CAPTCHAs. After 4 back-offs in a row, the loop stops itself and the dashboard shows "Google is blocking this IP. Stopped to protect it."
- **Block score** (Crawlee's session error-score idea): +1 per suspected-block run, −0.5 per clean run, floor 0. The pause doubles with each block as above; reaching a score of 4 triggers the auto-stop. Unlike a strict "4 in a row", one lucky run between blocks doesn't reset the danger.
- Also treat Google's block-page marker (a link to `google.com/policies/terms/` inside `#infoDiv0`, as listed in Crawlee's `blocked.py`) as a block signal if it shows up in gosom's output. Whether gosom logs page HTML is unverified; the builder checks this.
- The dashboard shows the run classification, the current pause, and the time of the next attempt.

### A6. LinkedIn via a search API: no login, never touching LinkedIn (approved by Sahil, 2026-11-20)

Sahil wants LinkedIn data "with the safest and without login way" (2026-11-20). Scraping LinkedIn directly stays an anti-goal. The safe path:

- For leads still missing an owner name after A3, ask an **official web-search API** something like `"<business name>" <city> site:linkedin.com/in` or `site:linkedin.com/company`.
- Store only what the search engine returns: the LinkedIn URL, plus the result title (usually "Name – Role – Company"). The owner name and role are parsed from that title. Owner Source = "LinkedIn search result".
- **LinkedIn's servers are never contacted.** No account, no cookies, no profile fetching, nothing to evade.
- A match is accepted only if the result mentions the business name. Anything else is skipped (anti-goal: inventing data).
- **Provider:** Brave Search API. Its old free tier was reportedly replaced in Feb 2026 by about $5 of monthly credit (~1,000 queries) and needs a card on file (third-party reports; Brave's own pricing page not checked). Google's Custom Search JSON API is closed to new customers and shuts down on 2027-01-01.
- **A hard monthly cap in our code** (default 1,000 queries) keeps usage inside the free credit. Lookups go to the best leads first (verified email or mobile, owner unknown).
- Self-hosted meta-search such as SearXNG is rejected. It just scrapes Google and Bing, the same blocking and ToS problems in disguise.
- Names are personal data: store only the URL, name and role, and note GDPR-type rules in the README.
- **Human step:** Sahil creates the Brave Search API account, adds a card, sets a spending limit in Brave's dashboard if offered, and puts the key in an environment variable (`BRAVE_API_KEY`). The key is never written into a file in the repo. Without a key, A6 is skipped and the dashboard says so.
- The dashboard shows lookups used this month out of the cap.

### A5. Export and dashboard changes

- Sahil's 18 columns keep their order and meaning. **Two columns are appended (my call):** `Social Links` (space-separated URLs) and `Sources` (e.g. `google_maps`, later `foursquare`, `osm`).
- Owner Name and Owner Source now come from rule A3 first.
- Dashboard additions:
  - The grid area for the current location and progress through it ("cell N of M", if gosom's log exposes it; otherwise places found so far).
  - Enrichment progress: sites crawled, names found.
  - Block status and current pause.

## Design: Phase B (after A ships)

### B1. Sources

- **Foursquare Open Places.** Free bulk dataset with name, address, coordinates, `tel`, `website`, `email` and social handles. Accessed through Foursquare's Iceberg catalog with a free portal token; queried with DuckDB, filtered to the location's bbox and the mapped categories. **Needs Sahil to create a Foursquare account and token** (a human step). The dataset's license wording must be checked before use; the builder confirms it and records it in the README.
- **OpenStreetMap through Overpass.** Free, no account. Query by bbox and mapped tags (e.g. `amenity=dentist`, `office=estate_agent`) for `phone`, `email`, `website`, `contact:*`. Respect the limits: one query at a time, back off 30 s on 429. Data is ODbL. Attribution goes in a `data/ATTRIBUTION.txt` written next to `leads.csv` and in the README, never as a row inside the CSV (my call; the CSV stays a plain table). The `Sources` column shows `osm` per lead. The README notes share-alike if the data is redistributed.

- **Tripadvisor by polite page reading** (picked by Sahil, 2026-11-20). Tripadvisor answered plain requests on 2026-11-20: a Dubai restaurants list page held 53 restaurant links, and a restaurant page had the phone in schema.org JSON-LD (`"telephone"`) plus a `mailto:` link, with no bot-wall marker. robots.txt allows generic crawlers on `/Restaurant_Review`, `/Hotel_Review` and `/Attraction_Review`.
  - **Coverage:** restaurants, hotels and attractions only. Other sectors skip Tripadvisor.
  - **Flow:** fetch the city's list page for the mapped kind, page through it, then fetch each listing's own page.
    - The page-offset URL pattern (believed to be `-oa30-`, `-oa60-`, …) is unverified; the builder confirms it on a real page.
    - A listing page is fetched once, ever. Its Tripadvisor id (`d32976128`) goes in a done set, the same way Google searches never repeat.
  - **Finding the city:** each location gets an optional "Tripadvisor city URL" field on the dashboard, pasted once (e.g. `…/Restaurants-g295424-Dubai_Emirate_of_Dubai.html`). The `g` id is read from it. A location without one skips Tripadvisor. (My call: it costs no extra requests and no Brave credit. Automatic lookup can come later.)
  - **Politeness:**
    - One request at a time to Tripadvisor, a randomized 3–6 s apart, with the same honest generic User-Agent as A3.
    - Follow robots.txt (cached), including `Crawl-delay`.
    - A daily cap of 500 listing pages (my call; tune on real data).
    - Runs after that location's Google run, like the other B sources.
  - **Extract** from the listing page's JSON-LD:
    - name, address, telephone, rating, review count, coordinates if present, and the email from `mailto:`.
    - The "visit website" link: use it if it is a plain URL. If it is an encoded redirect, decode it when the real URL is readable; otherwise leave Website empty rather than follow the redirect.
    - Store the raw JSON-LD in `lead_sources.raw`.
  - **Blocks:**
    - A 403, a 429, or a challenge page (markers such as DataDome, `captcha-delivery.com`, "Access denied") stops Tripadvisor for the cycle. Never solve or bypass a challenge.
    - It uses A4's back-off schedule (30/60/120/240 min) and block score, with a **separate score for Tripadvisor**, so a Tripadvisor block never pauses Google, and the reverse.
    - At a score of 4, Tripadvisor turns itself off and the dashboard says so.
  - **Verdict:** a Tripadvisor review count of 10+ counts as the reviews signal for leads Google didn't find (my call).
  - **Risk accepted by Sahil:** Tripadvisor's terms forbid automated collection. This is the same kind of risk as scraping Google Maps. The README says so.

### B2. Sector → category mapping

- A small editable file, `sources/categories.json`. It maps each sector to Foursquare category ids, OSM tags and, where it applies, a Tripadvisor kind (`Restaurants`, `Hotels`, `Attractions`). It is seeded for common sectors (dentist, clinic, real estate, gym, restaurant, salon, lawyer, accountant, hotel, car dealer).
- Sectors without a mapping skip B sources. The dashboard shows "no Foursquare/OSM/Tripadvisor mapping" next to such sectors.

### B3. Cross-source matching (dedupe stays always on)

- Every source writes into the same `leads` table. A new `lead_sources` table records `(lead_id, source, source_id, raw)`.
- Match an incoming record to an existing lead, in this order:
  1. Same normalized mobile or phone (E.164).
  2. Same website domain.
  3. Similar name (normalized, ≥ 0.85 similarity, stdlib `difflib`) **and** within 150 m.
- A match merges fields: fill empty ones only, and never overwrite a Google value with a weaker source. Otherwise insert a new lead with `place_id = "<source>:<source_id>"`.
- Leads found only through B have no Google reviews or rating. Their verdict uses the other signals.
- B runs per location after that location's Google run, so its fills improve the same export.

## Design: whole-country search (2026-11-20, between A and B)

**Intent.** Leaving cities empty used to add one plain search per sector for the whole country, capped by Google at ~120 places. Instead, an "All of <country>" target works through the whole country place by place, smallest areas first.

**Decided by Sahil:**
- Choosing a country with no cities adds an "All of <country>" target.
- Places come from GeoNames (free, CC BY 4.0), downloaded once into `data/geo/`. Neighborhood entries (`PPLX`) are skipped.
- **Order inside a country, smallest areas first** (2026-11-20, replacing "biggest state, biggest town first"): villages (1,000–15,000 people), then towns (15,000–100,000), then cities (100,000+), then each state, then the whole country. One place per run, with all sectors.
- A place already listed as a single city is searched once.
- **Single cities always run before any country's places** (2026-11-20). A city added mid-sweep runs at the next cycle. Countries run in list order.
- The dashboard shows the size of the job when a country is picked, and the run panel shows where the sweep is.

**Approaches considered for the queue order:** single cities first (picked: country sweeps are background work that can take weeks, and a city you add should not wait behind them); taking turns one run per target (everything moves, but a single city finishes slower); list order as before (a big country blocks everything under it).

**Approaches considered for "region":** villages of 1,000–15,000 people (picked: reaches rural businesses the towns miss, at the cost of many more runs); city neighborhoods (the city grid would cover them again); districts/counties (bigger than towns, so not the smallest level).

**Builder's calls (open to correction):**
- Files: `cities1000.zip`, `admin1CodesASCII.txt` and `countryInfo.txt` from `download.geonames.org/export/dump/`, fetched on first use (picking a country or planning its first place), written atomically. Historical, abandoned and destroyed places (`PPLH`, `PPLQ`, `PPLW`) are skipped along with `PPLX`.
- Inside each level, the least populated place first. A state's size is the sum of its places' populations (GeoNames has no state populations in these files).
- **Search areas:** villages and towns get a box around GeoNames' own coordinates (villages 4 km wide, one grid cell; towns 8 km), with no Nominatim lookup: there are thousands, Nominatim allows one request a second, and village names repeat. Cities, states and the country are geocoded through Nominatim as "Name, State" / "State" / "Country". A box over 2,500 km² (every state and country) falls back to plain searches, as before.
- **Names:** a place keeps its own name; if the name repeats in the country it becomes "Name, State"; if that still repeats, "Name, State 2", "Name, State 3" (least populated first). So search keys never collide.
- `cities.txt` stores the target as `*,SA`. The chip reads "All of Saudi Arabia".
- The dashboard shows, per level, how many places there are, the searches (× sectors), and a lower bound on days (one place per cycle, `PAUSE_MIN` between cycles). The run panel shows "Villages · 12 of 4,300 · Uttar Pradesh".
- Existing entries named after a whole country (added before this change) stay plain searches; re-add them to switch.

## Decided by Sahil

- Direction: A first, then B (2026-11-20).
- Leads are stored only; outreach is out of scope.
- **Overture Maps added** as a fourth source (2026-11-20): free, no account, read with DuckDB from the public bucket.
- WhatsApp checks are skipped. Mobiles are flagged as "likely WhatsApp".
- Dedupe is always on.
- The 18-column export (order and names).
- Free only; no CAPTCHA bypassing or anti-bot evasion (Sahil asked about CAPTCHA solvers on 2026-11-20; declined, and this anti-goal stands).
- Search depth: maximum volume (depth 10) with the grid.
- Tripadvisor: polite page reading (one request at a time, 3–6 s apart, robots.txt, the same back-off as Google, stop on any challenge page), not the official API (2026-11-20).
- **Free only, no card** (2026-11-20, later the same day): this overrides the card accepted for Brave below. Brave stays optional and off by default; the dashboard says it needs a card. SearXNG stays rejected (see A6).
- **Tripadvisor dropped** (2026-11-20, while building B): its first list page answered 403 with a DataDome challenge (`captcha-delivery.com`) to one polite request. Under the no-bypass rule that is a stop, like Yelp and Yellow Pages. B1's Tripadvisor design is kept for the record only.
- **Foursquare access** is the Hugging Face gated dataset (one form, no card, `HF_TOKEN`), not the Iceberg portal token. Labels are matched by whole category segments from `categories.json` (builder's call; check with `fsq.py check`).
- Matching radius for phone and website hits: 500 m when both places have coordinates, so chain branches stay apart (builder's call).
- LinkedIn: only through Brave's search API (A6), with no login, never fetching LinkedIn, capped at ~1,000 lookups a month to stay inside Brave's free credit. A card on file is accepted for this (2026-11-20).

## Left to the builder (my calls, open to correction)

- Default grid cell of 4 km, `-grid-strict` on, zoom per gosom's guidance.
- The two appended export columns: `Social Links`, `Sources`.
- Back-off schedule (30/60/120/240 min) and the 4-strikes auto-stop.
- HTML parsing library choice (smallest that works).
- Seed list for the category mapping.
- The name-similarity threshold of 0.85 and the 150 m radius for cross-source matching. Tune them on real data.

## Repos reviewed (2026-11-20)

Sahil asked for a review of 11 scraping repos. None is adopted as a dependency in round one; ideas are re-implemented in our small Python crawler.

| Repo | License | Verdict | What we take |
|---|---|---|---|
| firecrawl | AGPL-3.0; 8 GB+ self-host stack | Ideas only | `<base href>` link resolving, per-host robots cache, URL-token link ranking |
| crawl4ai | Apache-2.0 + attribution clause; stealth deps | Ideas only | Block-page detector, per-domain back-off, sitemap fallback, URL scorers |
| ScrapeGraphAI | MIT; LLM + evasion deps | Skip | Nothing; its value is LLM-driven |
| Scrapy | BSD-3; Twisted + 26 deps | Ideas only | AutoThrottle algorithm (optional), retryable status codes |
| crawlee / crawlee-python | Apache-2.0; bypass-by-default retries | Ideas only | Block error score, Google block marker, HTTP-first browser fallback (round two) |
| Colly | Apache-2.0; Go | Ideas only | Per-site parallelism 1 + delay + jitter |
| Playwright | Apache-2.0 | Round two | JS fallback with resource blocking |
| Puppeteer | Apache-2.0; Node | Skip | Duplicates Playwright |
| Scrapling | BSD-3; crawler needs its stealth fetchers | Ideas only | robots/Crawl-delay cache, Retry-After, checkpointed frontier |
| SeleniumBase | MIT; ~50 deps, evasion bundled | Skip | Nothing |
| Camoufox | MPL-2.0; anti-detect browser | Skip | Nothing (conflicts with the no-evasion rule) |
| EraSDR (Sahil's) | Private | Rules only | Named/obfuscated email harvesting, junk hosts, person-vs-company check, aggregator and blocked-domain skips (see A3). Not taken: Firecrawl (paid credits), the Crawl4AI server, LLM extraction, Postgres/Redis/Clerk. EraSDR never scrapes Google Maps itself; it finds URLs through Firecrawl Search. |
