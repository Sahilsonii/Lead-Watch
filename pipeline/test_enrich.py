"""Self-check for the polite website crawler, against a fake web. Run: python pipeline/test_enrich.py"""
import asyncio

import httpx

import enrich as e

waits = []


async def no_wait(s):
    waits.append(s)


e.pause = no_wait  # politeness delays are recorded, not slept

HTML = {"content-type": "text/html; charset=utf-8"}
TEXT = {"content-type": "text/plain"}


def web(routes):
    """Fake internet: routes maps URL -> response or list of responses (served in order)."""
    hits = []

    def handler(request):
        url = str(request.url)
        hits.append(url)
        r = routes.get(url, httpx.Response(404, headers=TEXT, text="nope"))
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        return r
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return client, hits


def crawl(routes, url="https://smile.ae/", phone="+971 4 566 2262"):
    client, hits = web(routes)
    result = asyncio.run(e.crawl_site(client, url, "Smile Clinic", phone, "AE", [".gov.ae"]))
    return result, hits


def page(body, head=""):
    return httpx.Response(200, headers=HTML, text=f"<html><head>{head}</head><body>{body} {'filler ' * 10}</body></html>")


ROBOTS_OK = httpx.Response(200, headers=TEXT, text="User-agent: *\nAllow: /\n")
LD = '<script type="application/ld+json">{"@type":"Dentist","founder":{"@type":"Person","name":"Sara Ali"}}</script>'

# happy path: homepage -> contact page with a named email; founder in JSON-LD; phone on the site
r, hits = crawl({
    "https://smile.ae/robots.txt": ROBOTS_OK,
    "https://smile.ae/": page('Smile Clinic, call 04 566 2262 <a href="/contact">Contact</a>', LD),
    "https://smile.ae/contact": page('<a href="mailto:jane@smile.ae">Jane Smith</a> <a href="https://instagram.com/smile">IG</a>'),
})
assert r["status"] == "ok" and r["pages"] == 2, r
assert r["emails"] == {"jane@smile.ae": "Jane Smith"}
assert r["owner"] == ("Sara Ali", "Founder", "Website structured data")
assert r["matches"] and r["social"] == ["https://instagram.com/smile"] and "+97145662262" in r["phones"]
assert waits and all(1 <= w <= 2 for w in waits)  # 1-2 s between pages of one site

# robots.txt says 403 -> refused, and no page is ever requested
r, hits = crawl({"https://smile.ae/robots.txt": httpx.Response(403, headers=TEXT, text="no")})
assert r["status"] == "refused" and hits == ["https://smile.ae/robots.txt"]

# robots.txt 5xx or timeout -> skip the site politely
r, hits = crawl({"https://smile.ae/robots.txt": [httpx.Response(503, headers=TEXT, text="x")] * 3})
assert r["status"] == "timeout" and all(h.endswith("robots.txt") for h in hits)
r, hits = crawl({"https://smile.ae/robots.txt": httpx.ConnectTimeout("slow")})
assert r["status"] == "timeout"

# robots.txt 404 -> everything allowed
r, _ = crawl({"https://smile.ae/": page("Smile Clinic")})
assert r["status"] == "ok"

# retries: 503, 503, 200 succeeds; Retry-After is honored but capped at 60 s
waits.clear()
r, hits = crawl({"https://smile.ae/robots.txt": ROBOTS_OK,
                 "https://smile.ae/": [httpx.Response(503, headers={"Retry-After": "5"}),
                                       httpx.Response(503, headers={"Retry-After": "999"}), page("Smile Clinic")]})
assert r["status"] == "ok" and hits.count("https://smile.ae/") == 3 and 5 in waits and 60 in waits
r, hits = crawl({"https://smile.ae/robots.txt": ROBOTS_OK, "https://smile.ae/": [httpx.Response(503)] * 4})
assert r["status"] == "timeout" and hits.count("https://smile.ae/") == 3  # 1 try + 2 retries
r, hits = crawl({"https://smile.ae/robots.txt": ROBOTS_OK, "https://smile.ae/": [httpx.Response(404)] * 2})
assert hits.count("https://smile.ae/") == 1                                 # 404 is not retried

# a challenge page marks the site blocked and is never mined
r, _ = crawl({"https://smile.ae/robots.txt": ROBOTS_OK, "https://smile.ae/": httpx.Response(
    403, headers=HTML, text='<html>jane@smile.ae <script src="https://ct.captcha-delivery.com/c.js"></script></html>')})
assert r["status"] == "blocked" and r["emails"] == {}

# robots Disallow is obeyed for discovered pages
r, hits = crawl({"https://smile.ae/robots.txt": httpx.Response(200, headers=TEXT, text="User-agent: *\nDisallow: /contact\n"),
                 "https://smile.ae/": page('Smile <a href="/contact">Contact</a>')})
assert "https://smile.ae/contact" not in hits and r["status"] == "ok"

# Crawl-delay raises the wait between pages
waits.clear()
r, _ = crawl({"https://smile.ae/robots.txt": httpx.Response(200, headers=TEXT, text="User-agent: *\nCrawl-delay: 7\n"),
              "https://smile.ae/": page('Smile <a href="/about">About</a>'), "https://smile.ae/about": page("Team")})
assert 7 in waits

# not HTML (PDF) -> nothing read, no crash; huge page is capped at 1 MB
r, _ = crawl({"https://smile.ae/robots.txt": ROBOTS_OK,
              "https://smile.ae/": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")})
assert r["status"] == "timeout" and r["pages"] == 0
r, _ = crawl({"https://smile.ae/robots.txt": ROBOTS_OK,
              "https://smile.ae/": httpx.Response(200, headers=HTML, text="<p>" + "x" * 3_000_000 + " big@smile.ae</p>")})
assert r["status"] == "ok" and "big@smile.ae" not in r["emails"]           # beyond the cap, never read

# a website that is really Facebook (directly or after a redirect) is skipped and kept as a social link
r, hits = crawl({}, url="https://www.facebook.com/smile")
assert r["status"] == "skipped" and r["social"] == ["https://www.facebook.com/smile"] and hits == []
r, _ = crawl({"https://smile.ae/robots.txt": ROBOTS_OK,
              "https://smile.ae/": httpx.Response(301, headers={"Location": "https://www.facebook.com/smile"}),
              "https://www.facebook.com/smile": page("fb")})
assert r["status"] == "skipped" and r["social"] == ["https://www.facebook.com/smile"]
r, hits = crawl({}, url="https://dha.gov.ae/")
assert r["status"] == "skipped" and hits == []
r, hits = crawl({}, url="")
assert r["status"] == "skipped"

# too few links on the homepage -> the sitemap finds the about page
r, hits = crawl({"https://smile.ae/robots.txt": ROBOTS_OK, "https://smile.ae/": page("Smile, no links"),
                 "https://smile.ae/sitemap.xml": httpx.Response(200, headers={"content-type": "application/xml"},
                     text="<urlset><url><loc>https://smile.ae/about-us</loc></url><url><loc>https://smile.ae/blog/1</loc></url></urlset>"),
                 "https://smile.ae/about-us": page("Lina Haddad, Founder")})
assert "https://smile.ae/about-us" in hits and "https://smile.ae/blog/1" not in hits
assert r["owner"] == ("Lina Haddad", "Founder", "Website team page")

# a bare domain without a scheme still works
r, _ = crawl({"https://smile.ae/robots.txt": ROBOTS_OK, "https://smile.ae/": page("Smile")}, url="smile.ae")
assert r["status"] == "ok"
print("all checks passed")
