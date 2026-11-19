"""Pure extraction rules for business websites: emails, phones, owner names, social links, block pages.

No network here; enrich.py fetches pages and hands their HTML to these functions.
Several rules are ported from EraSDR (lead-contact.ts, name-kind.ts, blocked-domains.ts, rank-urls.ts).
"""
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import phonenumbers

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Page:
    text: str = ""
    links: list = field(default_factory=list)   # (href, link text)
    ld_json: list = field(default_factory=list)  # raw <script type="application/ld+json"> bodies
    base: str | None = None


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.page, self.text, self.skip, self.ld, self.link = Page(), [], 0, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self.ld = []
        elif tag in ("script", "style", "noscript", "template"):
            self.skip += 1
        elif tag == "base" and a.get("href") and not self.page.base:
            self.page.base = a["href"]
        elif tag == "a" and a.get("href"):
            self.link = [a["href"], []]

    def handle_endtag(self, tag):
        if tag == "script" and self.ld is not None:
            self.page.ld_json.append("".join(self.ld))
            self.ld = None
        elif tag in ("script", "style", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
        elif tag == "a" and self.link:
            self.page.links.append((self.link[0], " ".join("".join(self.link[1]).split())))
            self.link = None

    def handle_data(self, data):
        if self.ld is not None:
            self.ld.append(data)
        elif not self.skip:
            self.text.append(data)
            if self.link:
                self.link[1].append(data)


def parse_page(html):
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    parser.page.text = " ".join(" ".join(parser.text).split())
    return parser.page


def norm(s):
    return " ".join((s or "").casefold().split())


# --- names -------------------------------------------------------------------------------------------
COMPANY_RE = re.compile(r"\b(limited|ltd|llc|inc|incorporated|plc|llp|gmbh|sarl|bv|pty|co|company|group|holdings|"
                        r"services|solutions|associates|partners|contractors|builders)\b\.?", re.I)
NOT_NAME_WORDS = {"our", "the", "team", "meet", "about", "contact", "us", "email", "mail", "call", "click", "here",
                  "write", "send", "book", "info", "home", "welcome", "message", "from", "clinic", "dental",
                  "medical", "center", "centre", "office", "real", "estate", "agency", "director", "founder",
                  # seen as false owners in the first live run: greetings, departments, hospital names
                  "hi", "hello", "hey", "dear", "patient", "patients", "hospital", "hospitals", "clinics", "healthcare",
                  "pharmacy", "department", "projects", "project", "special", "english", "arabic",
                  # job titles read as names ("General Dentist", "Managing Partner")
                  "general", "principal", "senior", "junior", "associate", "consultant", "specialist", "dentist",
                  "dentists", "orthodontist", "hygienist", "surgeon", "doctor", "manager", "assistant", "managing",
                  "partner", "owner", "ceo", "co-founder", "broker", "realtor", "agent", "agents"}


def looks_like_company(name, business):
    """True when a 'person' name is really a business (EraSDR's name-kind rule)."""
    name = (name or "").strip()
    letters = re.sub(r"[^A-Za-z]", "", name)
    return bool(COMPANY_RE.search(name)
                or (len(letters) >= 4 and letters.isupper() and len(name.split()) >= 2)
                or (norm(name) and norm(name) == norm(business)))


def clean_person(name, business=""):
    """A plausible person's name, or None (mailbox words, company names, emails, single words)."""
    name = " ".join((name or "").replace("\u00a0", " ").split()).strip(" ,.:;-\"'")
    words = name.split()
    if not 2 <= len(words) <= 4 or "@" in name or any(ch.isdigit() for ch in name):
        return None
    if any(w.strip(".").lower() in NOT_NAME_WORDS for w in words) or looks_like_company(name, business):
        return None
    return name


# --- emails ------------------------------------------------------------------------------------------
# Bounded parts (RFC limits) and a token-start lookbehind keep these linear on long minified text;
# unbounded "+" here backtracked quadratically (20k chars took >20 s).
LOCAL = r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}"
EMAIL = LOCAL + r"@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}"
LOOSE_RE = re.compile(EMAIL)
ANGLE_RE = re.compile(r"(?<![\w.'-])([A-Z][\w.'-]{0,40}(?:\s+[A-Z][\w.'-]{0,40}){1,3})\s*<\s*(" + EMAIL + r")\s*>")
# "at" must be bracketed, so plain sentences ("meet us at clinic dot com") never turn into addresses
OBFUSCATED_RE = re.compile("(" + LOCAL + r")\s*[\[({]\s*at\s*[\])}]\s*([A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,5})"
                           r"(?:\s*[\[({]?\s*dot\s*[\])}]?\s*([A-Za-z]{2,24}))?", re.I)


def emails_in(page):
    """{email: person name or None} from mailto links, 'Name <email>', bracketed obfuscation and plain text."""
    found = {}

    def remember(email, name=None):
        email = email.strip().strip(".").lower()
        if LOOSE_RE.fullmatch(email):
            found[email] = found.get(email) or clean_person(name)

    for href, text in page.links:
        if href.lower().startswith("mailto:"):
            remember(href[7:].split("?")[0], text)
    for name, email in ANGLE_RE.findall(page.text):
        remember(email, name)
    for local, domain, tld in OBFUSCATED_RE.findall(page.text):
        remember(f"{local}@{domain}.{tld}" if tld else f"{local}@{domain}")
    for email in LOOSE_RE.findall(page.text):
        remember(email)
    return found


def phones_in(page, region):
    """E.164 numbers from tel: links and the visible text."""
    out = []

    def add(number):
        if phonenumbers.is_valid_number(number):
            e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in out:
                out.append(e164)

    for href, _ in page.links:
        if href.lower().startswith("tel:"):
            try:
                add(phonenumbers.parse(href[4:], region or None))
            except phonenumbers.NumberParseException:
                pass
    for match in phonenumbers.PhoneNumberMatcher(page.text, region or "ZZ"):
        add(match.number)
    return out


# --- owners ------------------------------------------------------------------------------------------
# (pattern, display role, rank): lower rank wins
ROLE_TABLE = [(r"co-?founder", "Co-Founder", 0), (r"founder", "Founder", 0), (r"owner", "Owner", 0),
              (r"managing director", "Managing Director", 1), (r"chief executive officer|ceo", "CEO", 1),
              (r"principal", "Principal", 2), (r"director", "Director", 3), (r"partner", "Partner", 3)]
ROLES = tuple(r for _, r, _ in ROLE_TABLE)
ROLE_ALT = "|".join(p for p, _, _ in ROLE_TABLE)


def role_of(title):
    if isinstance(title, list):  # JSON-LD allows several job titles
        title = " ".join(t for t in title if isinstance(t, str))
    if not isinstance(title, str):
        return None
    for pattern, role, rank in ROLE_TABLE:
        if re.search(rf"\b(?:{pattern})\b", title, re.I):
            return role, rank
    return None


def json_ld(page):
    """Every JSON-LD object on the page, with @graph and lists flattened; invalid JSON is skipped."""
    out = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            out.append(node)
            walk(node.get("@graph", []))

    for raw in page.ld_json:
        try:
            walk(json.loads(raw))
        except ValueError:
            continue
    return out


def _is_person(node):
    t = node.get("@type")
    return isinstance(node, dict) and ("Person" == t or (isinstance(t, list) and "Person" in t))


def owner_from_json_ld(objs, business):
    """(name, role) from schema.org Person/founder/employee data, best role first."""
    found = []
    for o in objs:
        if _is_person(o) and role_of(o.get("jobTitle")):
            found.append((o.get("name"), *role_of(o.get("jobTitle"))))
        founders = o.get("founder")
        for f in founders if isinstance(founders, list) else [founders] if founders else []:
            found.append((f.get("name") if isinstance(f, dict) else f, "Founder", 0))
        employees = o.get("employee")
        for e in employees if isinstance(employees, list) else [employees] if employees else []:
            if isinstance(e, dict) and role_of(e.get("jobTitle")):
                found.append((e.get("name"), *role_of(e.get("jobTitle"))))
    found = [(clean_person(n, business), role, rank) for n, role, rank in found if isinstance(n, str)]
    found = sorted((f for f in found if f[0]), key=lambda f: f[2])
    return (found[0][0], found[0][1]) if found else None


# Latin-1 letters keep accented names whole (Zoë, Renée); the lookbehind stops a match starting mid-word
# ("Kenzie" inside "McKenzie"), so an unreadable name gives no owner rather than the tail of one.
NAME = r"(?<![\w'’-])((?:Dr\.?\s+)?[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ'’-]{1,30}(?:\s+[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ'’-]{1,30}){1,2})"
SEP = r"\s*[,:|\u2013\u2014-]\s*"
NAME_ROLE_RE = re.compile(NAME + SEP + rf"(?:our\s+|the\s+)?(?i:({ROLE_ALT}))\b")
ROLE_NAME_RE = re.compile(rf"(?i:\b({ROLE_ALT}))" + SEP + NAME)


def owner_from_text(text, business):
    """(name, role) next to a role word on a team/about page; None when the top role is shared (ambiguous)."""
    found = {}
    pairs = [(n, r) for n, r in NAME_ROLE_RE.findall(text or "")] + [(n, r) for r, n in ROLE_NAME_RE.findall(text or "")]
    for name, role_text in pairs:
        name = clean_person(name, business)
        role = role_of(role_text)
        if name and role:
            found.setdefault(role[1], {}).setdefault(name, role[0])
    if not found:
        return None
    top = found[min(found)]
    return next(iter(top.items())) if len(top) == 1 else None


# --- links -------------------------------------------------------------------------------------------
KEYWORDS = ("contact", "about", "team", "our-doctors", "doctors", "staff", "leadership", "founder",
            "enquiry", "get-in-touch", "reach-us", "book")
SOCIAL_HOSTS = ("instagram.com", "facebook.com", "linkedin.com", "tiktok.com", "youtube.com", "x.com", "twitter.com")
AGGREGATOR_HOSTS = SOCIAL_HOSTS + ("yelp.com", "tripadvisor.com", "trustpilot.com", "pinterest.com", "glassdoor.com",
                                   "indeed.com", "g.page", "linktr.ee")
GOOGLE_RE = re.compile(r"(^|\.)google\.[a-z.]+$")


def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _on(h, domains):
    return any(h == d or h.endswith("." + d) for d in domains)


def is_aggregator(url):
    """Social networks, review sites and Google pages are not the business's own website."""
    h = host(url)
    return bool(h) and (_on(h, AGGREGATOR_HOSTS) or bool(GOOGLE_RE.search(h)))


def social_links(page, page_url):
    out = []
    for href, _ in page.links:
        url = urljoin(page.base or page_url, href)
        if url.startswith("http") and _on(host(url), SOCIAL_HOSTS) and url not in out:
            out.append(url)
    return out


def candidate_links(page, page_url):
    """Same-site contact/about/team-style pages, best first (keyword hit, then shallow paths)."""
    site, base, seen, scored = host(page_url), page.base or page_url, {page_url.rstrip("/")}, []
    for href, text in page.links:
        if href.strip().lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urlunsplit(urlsplit(urljoin(base, href))._replace(fragment=""))
        if not url.startswith("http") or host(url) != site or url.rstrip("/") in seen:
            continue
        hay = (urlsplit(url).path + " " + text).lower()
        hits = sum(k in hay for k in KEYWORDS)
        if hits:
            seen.add(url.rstrip("/"))
            scored.append((-hits, urlsplit(url).path.count("/"), len(scored), url))
    return [u for *_, u in sorted(scored)]


def load_blocked_domains(path=ROOT / "blocked_domains.txt"):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines() if Path(path).exists() else []:
        v = re.sub(r"^https?://", "", line.strip().lower()).split("/")[0].removeprefix("www.")
        v = "." + v[2:] if v.startswith("*.") else v
        if v and "." in v and not v.startswith("#") and v not in out:
            out.append(v)
    return out


def is_blocked_domain(url, suffixes):
    h = host(url)
    return bool(h) and any(h.endswith(s) or h == s[1:] if s.startswith(".") else h == s or h.endswith("." + s)
                           for s in suffixes)


# --- checks ------------------------------------------------------------------------------------------
def site_matches_listing(page_text, phone, business):
    """The site shows the listing's phone (last 7 digits) or the business name."""
    tail = re.sub(r"\D", "", phone or "")[-7:]
    if len(tail) == 7 and tail in re.sub(r"\D", "", page_text or ""):
        return True
    return bool(norm(business)) and norm(business) in norm(page_text)


# Markers that only appear on challenge/interstitial pages (our own list, not copied from crawl4ai).
STRONG_MARKERS = ("cf-chl", "captcha-delivery.com", "px-captcha", "_incapsula_resource")
# Can also sit on a normal page (Cloudflare Turnstile on a contact form), so only counts on an error status.
WEAK_MARKERS = ("challenges.cloudflare.com", "attention required! | cloudflare", "checking your browser")


def is_block_page(status, html):
    low = (html or "").lower()
    if any(m in low for m in STRONG_MARKERS):
        return True
    if status in (403, 429, 503) and (any(m in low for m in WEAK_MARKERS)
                                      or ("access denied" in low and "reference #" in low)):
        return True
    return status == 200 and len(parse_page(html or "").text) < 50  # empty JS shell: nothing to read
