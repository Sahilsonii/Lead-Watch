"""Owner names of UK businesses from Companies House, the UK's official company register (free API key, no card).

Needs COMPANIES_HOUSE_KEY in the environment (never in a file). A lead is matched to one active company by name;
when two companies fit, or the name is too short to be sure, it gets no name rather than a stranger's. Owners
(people holding over 25%) come first, then directors of small companies. 600 requests per 5 minutes is the
register's limit; calls here are spaced to stay under it.
"""
import json
import os
import re
import time

import httpx

import contacts as c
import process as p
from grid import USER_AGENT

API = "https://api.company-information.service.gov.uk"
CALL_GAP_S = 0.6  # 600 requests / 300 s
MAX_DIRECTORS = 2  # more than this is a bigger firm: its directors are not the local owner
LEGAL_SUFFIX = re.compile(r"\b(limited|ltd|llp|plc|lp|l\.l\.p|co|company)\b\.?", re.I)
MAPS_EXTRAS = re.compile(r"\s+[-–|:]\s+|\s*\|\s*")  # "Sandersons London - Prime Central London Estate Agents"


def pause(seconds):  # one seam, so tests run without waiting
    time.sleep(seconds)


def core(name):
    words = re.sub(r"[^\w\s]", " ", LEGAL_SUFFIX.sub(" ", c.norm(name))).split()
    return " ".join(words)


def business_core(name):
    return core(MAPS_EXTRAS.split(name or "")[0])


def pick(items, business, postcode):
    """The company number of the one active company whose name fits the business, else None."""
    biz = business_core(business)
    fits = []
    for it in items:
        legal = core(it.get("title"))
        if it.get("company_status") != "active" or len(legal) < 4:
            continue
        same_place = postcode and c.norm((it.get("address") or {}).get("postal_code")).replace(" ", "") == postcode
        prefix = f"{biz} ".startswith(f"{legal} ") or f"{legal} ".startswith(f"{biz} ")
        if legal == biz or (prefix and (min(len(legal.split()), len(biz.split())) >= 2 or same_place)):
            fits.append(it["company_number"])
    return fits[0] if len(set(fits)) == 1 else None


def person(forename, surname):
    # shortcut: str.title() turns "McDONALD" into "Mcdonald"; good enough for an outreach greeting
    fix = lambda s: s.title() if s.isupper() or s.islower() else s
    return f"{fix(forename.split()[0])} {fix(surname)}" if forename and surname else None


def owners(psc):
    """Current individual owners (25%+ of shares or votes) as "First Last"."""
    out = [person(i.get("name_elements", {}).get("forename", ""), i.get("name_elements", {}).get("surname", ""))
           for i in psc.get("items") or []
           if i.get("kind") == "individual-person-with-significant-control" and not i.get("ceased_on")]
    return [n for n in out if n]


def directors(officers):
    """Current directors (or LLP members) of a small company as "First Last"; [] for a bigger firm."""
    out = []
    for o in officers.get("items") or []:
        if o.get("resigned_on") or o.get("officer_role") not in ("director", "llp-designated-member", "llp-member"):
            continue
        surname, _, forenames = (o.get("name") or "").partition(", ")
        if name := person(forenames, surname):
            out.append(name)
    return out if len(out) <= MAX_DIRECTORS else []


class Stop(Exception):
    pass


def get(client, path, params=None):
    """JSON from the register; None for 404 (no such register); Stop when the cycle must end."""
    try:
        r = client.get(path, params=params, timeout=15)
    except httpx.HTTPError:
        raise Stop("Companies House unreachable")
    if r.status_code == 401:
        raise Stop("Companies House key rejected")
    if r.status_code == 429:
        raise Stop("Companies House rate limit")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise Stop(f"Companies House error {r.status_code}")
    return r.json()


def lookup_pending(con, client, limit, stop, key=None):
    """Look up UK leads with no name as good as the register's. Both-channel leads go first."""
    stats = {"looked_up": 0, "names": 0, "skipped": None}
    if not key:
        return {**stats, "skipped": "no COMPANIES_HOUSE_KEY"}
    client.auth = httpx.BasicAuth(key, "")
    client.headers["User-Agent"] = USER_AGENT
    rows = con.execute(f"""
        SELECT place_id, name, raw, owner_source FROM leads WHERE country = 'GB' AND ch_checked_at IS NULL
        ORDER BY ({p.VERIFIED}) + (phone_e164 IS NOT NULL) DESC, reviews DESC""").fetchall()
    calls = 0
    for place_id, name, raw, stored in rows:
        if stats["looked_up"] >= limit or stop.is_set():
            break
        if not p.outranks("Companies House director", stored) and not p.outranks("Companies House owner", stored):
            continue  # a better name is already there; save the calls
        postcode = c.norm((json.loads(raw or "{}").get("complete_address") or {}).get("postal_code")).replace(" ", "")
        try:
            pause(CALL_GAP_S * bool(calls))
            calls += 1
            found = get(client, "/search/companies", {"q": business_core(name) or name, "items_per_page": 10})
            number = pick((found or {}).get("items") or [], name or "", postcode)
            names, role, source = [], None, None
            if number:
                pause(CALL_GAP_S)
                names = owners(get(client, f"/company/{number}/persons-with-significant-control") or {})[:3]
                role, source = "Owner", "Companies House owner"
                if not names or len(names) > 2:
                    pause(CALL_GAP_S)
                    names = directors(get(client, f"/company/{number}/officers") or {})
                    role, source = "Director", "Companies House director"
        except Stop as e:
            stats["skipped"] = str(e)
            break
        con.execute("UPDATE leads SET ch_checked_at = CURRENT_TIMESTAMP, ch_company = ? WHERE place_id = ?",
                    (number, place_id))
        if names and p.outranks(source, stored):
            con.execute("UPDATE leads SET owner_name = ?, owner_role = ?, owner_source = ? WHERE place_id = ?",
                        (" & ".join(names), role, source, place_id))
            stats["names"] += 1
        con.commit()
        stats["looked_up"] += 1
    return stats


if __name__ == "__main__":  # python pipeline/companies_house.py "Business Name"  -> what the register says
    import sys
    with httpx.Client(base_url=API) as cl:
        cl.auth = httpx.BasicAuth(os.environ["COMPANIES_HOUSE_KEY"], "")
        for it in (get(cl, "/search/companies", {"q": business_core(sys.argv[1])}) or {}).get("items", [])[:5]:
            print(it["company_number"], it["company_status"], it["title"], (it.get("address") or {}).get("postal_code"))
        print("picked:", pick(get(cl, "/search/companies", {"q": business_core(sys.argv[1])})["items"], sys.argv[1], ""))
