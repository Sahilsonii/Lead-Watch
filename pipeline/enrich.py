"""Polite multi-page website crawl: robots.txt, one request at a time per site, retries, no challenge-solving.

crawl_site() reads a business's homepage plus up to 3 contact/about/team pages and returns what contacts.py
finds there. It never passes a bot check: a challenge page just marks the site "blocked".
"""
import asyncio
import random
import re
import urllib.robotparser
from urllib.parse import urlsplit, urlunsplit

import httpx

import contacts as c
from grid import USER_AGENT

RETRY_CODES = {408, 429, 500, 502, 503, 504, 522, 524}
MAX_RETRIES = 2
TIMEOUT_S = 10
MAX_BYTES = 1_000_000
SITES_AT_ONCE = 8
EXTRA_PAGES = 3
MAX_RETRY_AFTER_S = 60
READABLE = ("html", "xml", "text/plain")
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


async def pause(seconds):  # one seam, so tests record waits instead of sleeping
    await asyncio.sleep(seconds)


def new_client():
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=TIMEOUT_S,
                             limits=httpx.Limits(max_connections=SITES_AT_ONCE * 2))


def retry_wait(response, attempt):
    try:
        return min(float(response.headers.get("Retry-After")), MAX_RETRY_AFTER_S)
    except (TypeError, ValueError):
        return 2 ** (attempt + 1)


async def fetch(client, url):
    """(status, final_url, text) or None for timeouts, network errors and non-text content (PDFs, images).
    Retries only RETRY_CODES, at most MAX_RETRIES times; reads at most MAX_BYTES."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with client.stream("GET", url, timeout=TIMEOUT_S) as r:
                if r.status_code in RETRY_CODES and attempt < MAX_RETRIES:
                    wait = retry_wait(r, attempt)
                else:
                    ctype = r.headers.get("content-type", "").lower()
                    if ctype and not any(t in ctype for t in READABLE):
                        return None
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            break
                    return r.status_code, str(r.url), bytes(body[:MAX_BYTES]).decode(r.encoding or "utf-8", "replace")
        except httpx.HTTPError:  # timeouts, refused connections, bad TLS, too many redirects
            return None
        await pause(wait)
    return None


async def robots_for(client, origin):
    """Rules for the site, or None when robots.txt can't be read (5xx/timeout): then skip the site (RFC 9309)."""
    got = await fetch(client, origin + "/robots.txt")
    if got is None or got[0] >= 500:
        return None
    rp = urllib.robotparser.RobotFileParser(origin + "/robots.txt")
    status, _, text = got
    if status in (401, 403):
        rp.disallow_all = True
    elif status >= 400:
        rp.allow_all = True
    else:
        rp.parse(text.splitlines())
    rp.modified()
    return rp


async def sitemap_links(client, origin, rp, wait):
    """Keyword pages listed in the site's sitemap (one sitemap file read, no index recursion)."""
    url = (rp.site_maps() or [origin + "/sitemap.xml"])[0]
    if not rp.can_fetch(USER_AGENT, url):
        return []
    await pause(wait)
    got = await fetch(client, url)
    if not got or got[0] >= 400:
        return []
    return [u for u in LOC_RE.findall(got[2])
            if c.host(u) == c.host(origin) and not u.lower().endswith(".xml")
            and any(k in urlsplit(u).path.lower() for k in c.KEYWORDS)]


def result(status, **found):
    return {"status": status, "emails": {}, "phones": [], "owner": None, "social": [], "matches": False,
            "pages": 0, **found}


async def crawl_site(client, url, business, phone, region, suffixes):
    url = (url or "").strip()
    if not url:
        return result("skipped")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    url = urlunsplit(parts._replace(path=parts.path or "/"))
    if c.is_aggregator(url):
        return result("skipped", social=[url])
    if c.is_blocked_domain(url, suffixes):
        return result("skipped")

    origin = f"{parts.scheme}://{parts.netloc}"
    rp = await robots_for(client, origin)
    if rp is None:
        return result("timeout")
    if not rp.can_fetch(USER_AGENT, url):
        return result("refused")
    wait = max(random.uniform(1, 2), float(rp.crawl_delay(USER_AGENT) or 0))

    got = await fetch(client, url)
    if got is None:
        return result("timeout")
    status, final, html = got
    if c.is_aggregator(final):
        return result("skipped", social=[final])
    if c.is_block_page(status, html):
        return result("blocked")
    if status >= 400:
        return result("timeout")

    pages = [(final, c.parse_page(html))]
    links = c.candidate_links(pages[0][1], final)
    if len(links) < 3:
        links += [u for u in await sitemap_links(client, origin, rp, wait) if u not in links]
    status = "ok"
    for link in links[:EXTRA_PAGES]:
        if not rp.can_fetch(USER_AGENT, link):
            continue
        await pause(wait)
        got = await fetch(client, link)
        if got is None:
            continue
        if c.is_block_page(got[0], got[2]):
            status = "blocked"  # stop here; earlier pages were real pages, so keep what they gave
            break
        if got[0] < 400:
            pages.append((got[1], c.parse_page(got[2])))

    emails, phones, social = {}, [], []
    for page_url, page in pages:
        for email, name in c.emails_in(page).items():
            emails[email] = emails.get(email) or name
        phones += [n for n in c.phones_in(page, region) if n not in phones]
        social += [s for s in c.social_links(page, page_url) if s not in social]
    ld = c.owner_from_json_ld([o for _, p in pages for o in c.json_ld(p)], business)
    text = c.owner_from_text(" ".join(p.text for _, p in pages), business)
    named = next(((n, "Email contact") for n in emails.values() if n), None)
    owner = (ld and (*ld, "Website structured data")) or (text and (*text, "Website team page")) \
        or (named and (*named, "Website email link"))
    return result(status, emails=emails, phones=phones, social=social, owner=owner, pages=len(pages),
                  matches=any(c.site_matches_listing(p.text, phone, business) for _, p in pages))
