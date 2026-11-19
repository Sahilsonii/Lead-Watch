"""Lead database: queue searches, ingest scraper output, verify emails, export verified leads.

Usage:
  python pipeline/process.py next-batch N   # write data/batch.txt, print how many searches it holds
  python pipeline/process.py ingest FILE    # load scraper JSON into data/leads.db
  python pipeline/process.py export         # write data/leads.csv
"""
import csv
import json
import os
import re
import smtplib
import socket
import sqlite3
import sys
import uuid
from pathlib import Path

import dns.resolver
import phonenumbers
from phonenumbers import PhoneNumberType as T

import contacts

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SMTP_CHECK = os.environ.get("SMTP_CHECK") == "1"  # needs outbound port 25, which most clouds block
EMAIL_RE = re.compile(r"^[\w.+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
# Website crawls pick up image names ("logo@2x.png") and theme placeholders; drop them.
# Hosts like sentry/wixpress/cloudfront/w3 show up in site code, never as a person's mailbox (list from EraSDR).
JUNK_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg)$|@(example|domain|email|yourdomain)\."
                     r"|@([\w-]+\.)*(sentry|wixpress|googleapis|gstatic|cloudfront|schema|w3)\.", re.I)
WHATSAPP_TYPES = {T.MOBILE, T.FIXED_LINE_OR_MOBILE}


def ensure_columns(con, table, cols):
    """Add missing columns, so older databases keep working as the schema grows."""
    have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    for name, kind in cols.items():
        if name not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def db(path=None):
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(path or DATA / "leads.db")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS queries(q TEXT PRIMARY KEY, done_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS leads(
            place_id TEXT PRIMARY KEY, name TEXT, category TEXT, address TEXT, country TEXT,
            website TEXT, phone TEXT, phone_e164 TEXT, maps_link TEXT, rating REAL, reviews INTEGER,
            email TEXT, email_status TEXT, whatsapp INTEGER, query TEXT,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP, raw TEXT);
        CREATE TABLE IF NOT EXISTS locations(
            name TEXT, cc TEXT, bbox TEXT, area_km2 REAL, PRIMARY KEY(name, cc));
    """)
    ensure_columns(con, "leads", {  # databases from before the 18- and 20-column exports
        "raw": "TEXT", "owner_name": "TEXT", "owner_role": "TEXT", "owner_source": "TEXT", "social_links": "TEXT",
        "site_status": "TEXT", "site_matches": "INTEGER", "enriched_at": "TEXT", "linkedin_url": "TEXT",
        "linkedin_checked_at": "TEXT", "sources": "TEXT DEFAULT 'google_maps'"})
    return con


def lines(name):
    return [l.strip() for l in (ROOT / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def all_searches():
    # "#!#AE|dentist|Dubai" is gosom's custom input_id. It comes back on every result, carrying the
    # country (for phone parsing), sector and searched location. Lowercased so "Dentist" == "dentist".
    return list(dict.fromkeys(
        f"{niche.lower()} in {city.strip()} #!#{cc.strip().upper()}|{niche.lower()}|{city.strip()}"
        for city, cc in (l.rsplit(",", 1) for l in lines("cities.txt"))
        for niche in lines("niches.txt")))


def split_input(input_id):
    """'AE|dentist|Dubai' -> ('AE', 'dentist', 'Dubai'); older ids were just 'AE'."""
    parts = (input_id or "").split("|") + ["", ""]
    return parts[0], parts[1], parts[2]


def done_searches():
    """Finished searches, compared on the text Google sees ("dentist in Dubai"), not on the id tag."""
    return {r[0].split(" #!#")[0] for r in db().execute("SELECT q FROM queries")}


def next_batch(n):
    done = done_searches()
    batch = [q for q in all_searches() if q.split(" #!#")[0] not in done][:n]
    (DATA / "batch.txt").write_text("\n".join(batch) + "\n", encoding="utf-8")
    return len(batch)


def to_e164(phone, region):
    """Return +E.164 if the number could be on WhatsApp, else None."""
    try:
        p = phonenumbers.parse(phone or "", region or None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(p) or phonenumbers.number_type(p) not in WHATSAPP_TYPES:
        return None
    return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)


def pretty_phone(phone, region):
    """Any phone, mobile or landline, in its country's standard grouping with the country code
    ('045662262' -> '+971 4 566 2262'). Numbers that don't parse are returned as they were."""
    try:
        n = phonenumbers.parse(phone or "", region or None)
    except phonenumbers.NumberParseException:
        return (phone or "").strip()
    if not phonenumbers.is_possible_number(n):
        return (phone or "").strip()
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


_mx = {}


def mx_host(domain):
    if domain not in _mx:
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=6)
            _mx[domain] = str(min(answers, key=lambda r: r.preference).exchange).rstrip(".")
        except Exception:  # NXDOMAIN, no MX, timeout: all mean "can't receive mail"
            _mx[domain] = None
    return _mx[domain]


def verify_email(email):
    """invalid | mx_ok (domain takes mail) | smtp_ok (mailbox accepted) | catch_all (domain accepts anything)."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or JUNK_RE.search(email):
        return "invalid"
    domain = email.split("@")[1]
    host = mx_host(domain)
    if not host:
        return "invalid"
    if not SMTP_CHECK:
        return "mx_ok"
    try:
        with smtplib.SMTP(host, 25, timeout=10) as s:
            s.helo(socket.getfqdn())
            s.mail(f"verify@{socket.getfqdn()}")
            code = s.rcpt(email)[0]
            if code >= 500:
                return "invalid"
            if code != 250:
                return "mx_ok"  # greylisted / temporary: keep the MX-level answer
            return "catch_all" if s.rcpt(f"{uuid.uuid4().hex}@{domain}")[0] == 250 else "smtp_ok"
    except (OSError, smtplib.SMTPException):
        return "mx_ok"  # port 25 blocked or server hung up


RANK = {"smtp_ok": 3, "catch_all": 2, "mx_ok": 1, "invalid": 0}


def best_email(emails, website):
    """Pick the strongest email, preferring ones on the business's own domain."""
    site = re.sub(r"^(https?://)?(www\.)?", "", (website or "").lower()).split("/")[0]
    best = (None, None)
    for e in sorted(set(emails or []), key=lambda e: not (site and e.lower().endswith("@" + site))):
        status = verify_email(e)
        if best[1] is None or RANK[status] > RANK[best[1]]:
            best = (e.strip().lower(), status)
        if status == "smtp_ok":
            break
    return best


def read_entries(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(l) for l in text.splitlines() if l.strip()]  # JSON Lines


def ingest(path, done_keys=()):
    """Upsert places by Google place id (so re-scrapes never duplicate), then mark done_keys searched.
    Returns (new, duplicates)."""
    con = db()
    new = dup = 0
    for e in read_entries(path):
        key = e.get("place_id") or e.get("data_id") or e.get("link")
        if not key:
            continue
        if con.execute("SELECT 1 FROM leads WHERE place_id = ?", (key,)).fetchone():
            dup += 1
        else:
            new += 1
        email, status = best_email(e.get("emails"), e.get("web_site"))
        phone_e164 = to_e164(e.get("phone"), split_input(e.get("input_id"))[0])
        con.execute("""
            INSERT INTO leads(place_id, name, category, address, country, website, phone, phone_e164,
                              maps_link, rating, reviews, email, email_status, query, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(place_id) DO UPDATE SET
                email = COALESCE(excluded.email, email),
                email_status = COALESCE(excluded.email_status, email_status),
                phone_e164 = COALESCE(excluded.phone_e164, phone_e164),
                rating = excluded.rating, reviews = excluded.reviews, raw = excluded.raw,
                query = CASE WHEN excluded.query LIKE '%|%' THEN excluded.query ELSE query END""",
            (key, e.get("title"), e.get("category"), e.get("address"),
             (e.get("complete_address") or {}).get("country"), e.get("web_site"), e.get("phone"),
             phone_e164, e.get("link"), e.get("review_rating"), e.get("review_count"),
             email, status, e.get("input_id"), json.dumps(e)))
    con.executemany("INSERT OR IGNORE INTO queries(q) VALUES (?)", [(k,) for k in done_keys])
    con.commit()
    return new, dup


def save_enrichment(con, place_id, res):
    """Store one website crawl result (enrich.crawl_site). Returns True when an owner name was stored."""
    name, website, raw, phone, query = con.execute(
        "SELECT name, website, raw, phone, query FROM leads WHERE place_id = ?", (place_id,)).fetchone()
    maps_emails = json.loads(raw or "{}").get("emails") or []
    email, status = best_email(list(maps_emails) + list(res["emails"]), website)
    owner = res["owner"]
    if owner and contacts.looks_like_company(owner[0], name or ""):
        owner = None
    site_phone = None if phone or not res["phones"] else res["phones"][0]
    owner_sql = "owner_name = ?, owner_role = ?, owner_source = ?," if owner else ""  # never erase an earlier name
    con.execute(f"""
        UPDATE leads SET email = COALESCE(?, email), email_status = COALESCE(?, email_status), {owner_sql}
            social_links = ?, site_status = ?, site_matches = ?,
            enriched_at = CURRENT_TIMESTAMP, phone = COALESCE(?, phone), phone_e164 = COALESCE(phone_e164, ?)
        WHERE place_id = ?""",
        (email, status, *(owner or ()), " ".join(res["social"]), res["status"],
         int(res["matches"]), site_phone, site_phone and to_e164(site_phone, split_input(query)[0]), place_id))
    con.commit()
    return bool(owner)


VERIFIED = "COALESCE(email_status, '') IN ('smtp_ok', 'catch_all', 'mx_ok')"
GOOD_EMAIL = {"smtp_ok", "catch_all", "mx_ok"}
COLUMNS = ["Business Name", "Owner Name", "Owner Source", "Address/Location", "Postcode", "City", "Region",
           "Business Sector", "Category", "Phone", "Email", "WhatsApp", "Website", "Total Google Reviews",
           "Rating", "Short Brief", "Date Added", "Authenticity Verdict", "Social Links", "Sources"]
OWNER_RANK = ["Website structured data", "Website team page", "Business name", "Website email link",
              "LinkedIn search result", "Email address (guess)"]
GENERIC_MAILBOXES = {"info", "contact", "hello", "admin", "office", "sales", "support", "enquiries", "enquiry",
                     "inquiry", "inquiries", "booking", "bookings", "reception", "mail", "team", "help", "care",
                     "appointments", "appointment", "clinic", "marketing", "hr", "jobs", "careers", "accounts",
                     "billing", "service", "services", "customerservice", "noreply", "webmaster", "dental",
                     "manager", "management", "frontdesk", "general", "query", "queries", "feedback", "dubai"}
DR_RE = re.compile(r"\bDr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")


def owner_of(e, email):
    """Best-effort person behind the business. Google's listing owner is the business account itself."""
    m = DR_RE.search(e.get("title") or "")
    if m:
        return "Dr. " + re.sub(r"'s$", "", m.group(1)), "Business name"
    local, _, domain = (email or "").partition("@")
    parts = re.split(r"[._-]", local.lower())
    biz = (e.get("title") or "").lower() + domain
    if 1 <= len(parts) <= 2 and all(p.isalpha() and len(p) >= 3 and p not in GENERIC_MAILBOXES and p not in biz
                                    for p in parts):
        return " ".join(p.capitalize() for p in parts), "Email address (guess)"
    return "", "Not found"


def lead_owner(e, r):
    """Best owner name across the website crawl, the business name and the email (see OWNER_RANK)."""
    found = [owner_of(e, r.get("email")), (r.get("owner_name"), r.get("owner_source"))]
    return min((o for o in found if o[0] and o[1] in OWNER_RANK), key=lambda o: OWNER_RANK.index(o[1]),
               default=("", "Not found"))


def own_website(r):
    """The Maps website, unless it is really a Facebook/Instagram/directory page (that goes to Social Links)."""
    site = r.get("website") or ""
    return "" if contacts.is_aggregator(site) else site


def verdict(e, r):
    """Authenticity from the share of public signals that apply: claimed listing and reviews (Google leads),
    website, verified email, valid phone, and the website showing the listing (when it was crawled)."""
    if "closed" in (e.get("status") or "").lower():
        return "Closed"
    site = re.sub(r"^(https?://)?(www\.)?", "", own_website(r).lower()).split("/")[0]
    signals = {
        "website": bool(site),
        "email on own domain" if site and (r["email"] or "").endswith("@" + site) else "verified email":
            r["email_status"] in GOOD_EMAIL,
        "valid phone": bool(r["phone_e164"] or r["phone"]),
    }
    if "google_maps" in (r.get("sources") or "google_maps"):
        signals = {"owner-claimed": bool(e.get("owner")),
                   f"{r['reviews'] or 0} reviews": (r["reviews"] or 0) >= 10, **signals}
    if r.get("site_status") == "ok":
        signals["site matches listing"] = bool(r.get("site_matches"))
    hits = [k for k, ok in signals.items() if ok]
    share = len(hits) / len(signals)
    label = "Verified" if share >= 2 / 3 else "Likely genuine" if share >= 1 / 3 else "Weak"
    return f"{label} ({', '.join(hits)})" if hits else label


def brief(e, r, city):
    desc = " ".join((e.get("description") or "").split())
    if desc:
        return desc[:220]
    where = f" in {city}" if city else ""
    rated = f", rated {r['rating']} from {r['reviews']} Google reviews" if r["rating"] else ", no Google reviews yet"
    return f"{r['category'] or 'Business'}{where}{rated}."


def lead_row(r):
    r = dict(r)
    e = json.loads(r["raw"] or "{}")
    addr = e.get("complete_address") or {}
    cc, sector, searched = split_input(e.get("input_id") or r["query"])
    city = addr.get("city") or searched or addr.get("state") or ""
    owner, source = lead_owner(e, r)
    website = own_website(r)
    links = [r["website"] if r["website"] and not website else None,
             *(r.get("social_links") or "").split(), r.get("linkedin_url")]
    return [r["name"], owner, source, r["address"], addr.get("postal_code") or "", city, addr.get("state") or "",
            sector, r["category"], pretty_phone(r["phone"], cc), r["email"] if r["email_status"] in GOOD_EMAIL else "",
            pretty_phone(r["phone_e164"], cc), website, r["reviews"], r["rating"], brief(e, r, city),
            (r["scraped_at"] or "")[:10], verdict(e, r), " ".join(dict.fromkeys(l for l in links if l)),
            r.get("sources") or "google_maps"]


def dedupe_contacts(rows):
    """Keep the first row per email and per phone, so chains and duplicate listings appear once."""
    seen, out = set(), []
    for r in rows:
        contacts = {c for c in (r["email"], r["phone_e164"] or re.sub(r"\D", "", r["phone"] or "")) if c}
        if contacts & seen:
            continue
        seen |= contacts
        out.append(r)
    return out


def export():
    con = db()
    con.row_factory = sqlite3.Row
    # Leads with a verified email or a mobile (likely WhatsApp); both-channel leads first.
    rows = con.execute(f"""
        SELECT * FROM leads WHERE {VERIFIED} OR phone_e164 IS NOT NULL
        ORDER BY ({VERIFIED}) + (phone_e164 IS NOT NULL) DESC, reviews DESC""").fetchall()
    rows = dedupe_contacts(rows)
    with open(DATA / "leads.csv", "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(lead_row(r) for r in rows)
    return len(rows)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "next-batch":
        print(next_batch(int(sys.argv[2])))
    elif cmd == "ingest":
        print("new %d, duplicates %d" % ingest(sys.argv[2], [q.split(" #!#")[0] for q in lines("data/batch.txt")]))
    elif cmd == "export":
        print(f"exported {export()} leads to data/leads.csv")
    else:
        sys.exit(__doc__)
