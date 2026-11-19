"""Owner names from LinkedIn profiles as Brave Search lists them. LinkedIn itself is never requested.

Needs BRAVE_API_KEY in the environment (never in a file). Calls are capped per month (BRAVE_MONTHLY_CAP).
"""
import os
import re
import time

import httpx

import contacts as c
import process as p
from grid import USER_AGENT

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MONTHLY_CAP = int(os.environ.get("BRAVE_MONTHLY_CAP", 1000))
CALL_GAP_S = 1.1
SEPARATORS = re.compile(r"\s+[-–|]\s+")
PROFILE_RE = re.compile(r"^https://([a-z]{2,3}\.|www\.)?linkedin\.com/(in|company)/", re.I)
ELIGIBLE_SOURCES = {"Not found", "Email address (guess)"}  # anything better is never replaced by a search guess


def month():
    return time.strftime("%Y-%m")


def usage(con):
    """(calls this month, monthly cap)."""
    con.execute("CREATE TABLE IF NOT EXISTS linkedin_usage(month TEXT PRIMARY KEY, used INTEGER)")
    row = con.execute("SELECT used FROM linkedin_usage WHERE month = ?", (month(),)).fetchone()
    return (row[0] if row else 0), MONTHLY_CAP


def query_for(name, city):
    return f'"{name}" {city} site:linkedin.com'


def parse_title(title):
    """'Sara Ali - Founder - Smile Clinic | LinkedIn' -> ('Sara Ali', 'Founder')."""
    parts = [s.strip() for s in SEPARATORS.split(title or "") if s.strip()]
    if parts and parts[-1].lower() == "linkedin":
        parts.pop()
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


def pick(results, business):
    """(url, name, role) of the best result that mentions the business: a person's profile first, then the
    company page (URL only). None when nothing fits."""
    wanted = c.norm(business)
    fits = [r for r in results if PROFILE_RE.match(r.get("url") or "")
            and wanted and wanted in c.norm(f"{r.get('title')} {r.get('description')}")]
    fits.sort(key=lambda r: "/in/" not in r["url"])
    if not fits:
        return None
    best = fits[0]
    if "/in/" not in best["url"]:
        return best["url"], None, None
    name, role = parse_title(best["title"])
    person = c.clean_person(name, business)
    return (best["url"], person, role or None) if person else (best["url"], None, None)


def lookup_pending(con, client, limit, stop):
    """Search Brave for owners of leads the website crawl found no name for. Both-channel leads go first."""
    stats = {"looked_up": 0, "names": 0, "skipped": None}
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return {**stats, "skipped": "no BRAVE_API_KEY"}
    headers = {"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": USER_AGENT}
    rows = con.execute(f"""
        SELECT place_id, name, email, query, owner_name, owner_source FROM leads
        WHERE linkedin_checked_at IS NULL AND enriched_at IS NOT NULL AND ({p.VERIFIED} OR phone_e164 IS NOT NULL)
        ORDER BY ({p.VERIFIED}) + (phone_e164 IS NOT NULL) DESC, reviews DESC""").fetchall()
    calls = 0
    for place_id, name, email, query, owner_name, owner_source in rows:
        if calls >= limit or stop.is_set():  # calls, not successes: failed calls still cost time and credit
            break
        stored = {"email": email, "owner_name": owner_name, "owner_source": owner_source}
        if p.lead_owner({"title": name}, stored)[1] not in ELIGIBLE_SOURCES:
            continue
        if usage(con)[0] >= MONTHLY_CAP:
            stats["skipped"] = "monthly cap"
            break
        if calls:
            time.sleep(CALL_GAP_S)
        calls += 1
        try:
            r = client.get(BRAVE_URL, headers=headers, timeout=15,
                           params={"q": query_for(name, p.split_input(query)[2]), "count": 5})
        except httpx.HTTPError:
            stats["skipped"] = "brave unreachable"
            break
        if r.status_code in (402, 429):  # credit used up or rate-limited: stop, leave this lead unchecked
            stats["skipped"] = "brave limit"
            break
        if r.status_code in (401, 403):  # bad or revoked key: every other call would fail the same way
            stats["skipped"] = f"brave error {r.status_code}"
            break
        if r.status_code != 200:
            continue
        con.execute("INSERT INTO linkedin_usage(month, used) VALUES (?, 1) "
                    "ON CONFLICT(month) DO UPDATE SET used = used + 1", (month(),))
        found = pick((r.json().get("web") or {}).get("results") or [], name or "")
        url, person, role = found or (None, None, None)
        con.execute("UPDATE leads SET linkedin_checked_at = CURRENT_TIMESTAMP, linkedin_url = ? WHERE place_id = ?",
                    (url, place_id))
        if person:
            con.execute("UPDATE leads SET owner_name = ?, owner_role = ?, owner_source = 'LinkedIn search result' "
                        "WHERE place_id = ?", (person, role, place_id))
            stats["names"] += 1
        con.commit()
        stats["looked_up"] += 1
    return stats
