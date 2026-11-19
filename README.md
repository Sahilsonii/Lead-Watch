# Lead Watch: Google Maps → verified leads

A dashboard with Start/Stop that scrapes Google Maps 24/7 for the sectors and countries you pick, verifies emails, and exports deduplicated leads to `data/leads.csv`.

Each cycle (every 30 min by default) it:

1. Takes the next 5 unsearched "sector in location" searches from your targets.
2. Scrapes them with [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) in Docker, including emails found on each business website.
3. Verifies emails (junk filter + MX lookup, optional SMTP mailbox check) and marks mobile numbers, which are likely on WhatsApp. No WhatsApp account is used.
4. Exports every lead with a verified email or a mobile number, best leads first.

## Dedupe (always on)

- **Searches:** a "sector in location" search runs once, ever.
- **Places:** stored by Google place id, so a business found by two searches is saved once.
- **Contacts:** in the export, a repeated email or phone (chains, duplicate listings) appears once.

## Export columns

Business Name, Owner Name, Owner Source, Address/Location, Postcode, City, Region, Business Sector, Category, Phone, Email, WhatsApp, Website, Total Google Reviews, Rating, Short Brief, Date Added, Authenticity Verdict.

- **Owner Name** is best effort. Google only exposes the business account, so the name comes from a "Dr. …" in the business name, or from a personal-looking email like `sarah.jones@…`. **Owner Source** says which, or "Not found".
- **WhatsApp** is the mobile number in international format. It is likely on WhatsApp but not checked.
- **Postcode / City** are empty where Google has none (for example the UAE). City then falls back to the searched location.
- **Authenticity Verdict**: Verified (4+ signals), Likely genuine (2–3), Weak, or Closed. The signals are listed: owner-claimed listing, 10+ reviews, website, verified email, valid phone.

## Run it

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python pipeline\dashboard.py
```

Open http://localhost:8765, set sectors and locations, press **Save targets**, then **Start**. Docker must be running.

Knobs (environment variables): `BATCH` (searches per cycle, 5), `CONCURRENCY` (2), `PAUSE_MIN` (30), `SMTP_CHECK=1`.

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

- **Google:** keep `CONCURRENCY` at 1–2 and the pause in place. If results drop to zero, Google is blocking the IP: slow down or add proxies (gosom's `-proxies` flag). Never solve or bypass CAPTCHAs.
- **Email:** most clouds block port 25, so SMTP checks usually fall back to `mx_ok` (the domain receives mail; the mailbox itself is not proven).
- **Law:** scraping may breach Google's terms, and contacting leads is regulated (GDPR, PECR, CAN-SPAM, CASL). WhatsApp bans accounts that send unsolicited bulk messages.

## Tests

`cd pipeline && python test_process.py`
