"""Self-check for owner names from Companies House, against canned API answers. Run: python pipeline/test_companies_house.py"""
import json
import tempfile
import threading
from pathlib import Path

import httpx

import companies_house as ch
import process as p

p.DATA = Path(tempfile.mkdtemp())
con = p.db()
ch.pause = lambda s: None


def active(title, number, postcode="SW1A 1AA", status="active"):
    return {"title": title, "company_number": number, "company_status": status, "address": {"postal_code": postcode}}


# the legal name is the business name minus "Ltd" and Maps' extras; one fit only, never a guess between two
assert ch.business_core("Marsh & Parsons London Bridge Estate Agents") == "marsh parsons london bridge estate agents"
assert ch.business_core("Sandersons London - Prime Central London Estate Agents") == "sandersons london"
assert ch.pick([active("MARSH & PARSONS LIMITED", "1")], "Marsh & Parsons London Bridge Estate Agents", "") == "1"
assert ch.pick([active("LEO NEWMAN ESTATES LTD", "2")], "Leo Newman", "") == "2"
assert ch.pick([active("GRANGE LONDON LIMITED", "3", status="dissolved")], "Grange London Limited", "") is None
assert ch.pick([active("SMITH LTD", "4"), active("SMITH LIMITED", "5")], "Smith", "") is None  # two fits
assert ch.pick([active("HUDSONS LTD", "6")], "Hudsons Property", "") is None  # one word, elsewhere: not sure
assert ch.pick([active("HUDSONS LTD", "6", "W1T 4PG")], "Hudsons Property", "w1t4pg") == "6"  # same postcode
assert ch.pick([active("GO LIMITED", "7")], "Go View London", "") is None  # too short to trust

# people: owners (25%+) first, then directors; names read "First Last"
psc = {"items": [{"kind": "individual-person-with-significant-control",
                  "name_elements": {"title": "Mr", "forename": "Leo", "middle_name": "James", "surname": "NEWMAN"}},
                 {"kind": "individual-person-with-significant-control", "ceased_on": "2020-01-01",
                  "name_elements": {"forename": "Old", "surname": "Owner"}},
                 {"kind": "corporate-entity-person-with-significant-control", "name": "LN HOLDINGS LTD"}]}
officers = {"items": [{"name": "NEWMAN, Leo James", "officer_role": "director"},
                      {"name": "PATEL, Asha", "officer_role": "director"},
                      {"name": "GONE, Ex", "officer_role": "director", "resigned_on": "2019-01-01"},
                      {"name": "CO SEC LTD", "officer_role": "corporate-secretary"}]}
assert ch.owners(psc) == ["Leo Newman"]
assert ch.directors(officers) == ["Leo Newman", "Asha Patel"]
assert ch.directors({"items": [{"name": f"P{i}, Ann", "officer_role": "director"} for i in range(4)]}) == []  # big firm


def lead(place_id, name, cc="GB", postcode="W1T 4PG"):
    raw = {"title": name, "complete_address": {"postal_code": postcode, "country": cc}}
    con.execute("INSERT INTO leads(place_id, name, country, query, raw) VALUES (?,?,?,?,?)",
                (place_id, name, cc, f"{cc}|real estate agency|London", json.dumps(raw)))


lead("a", "Leo Newman")
lead("b", "Marsh & Parsons London Bridge Estate Agents")
lead("c", "Smile Dental", cc="AE")  # not a UK business: never looked up
lead("d", "Nowhere Estates")
con.execute("UPDATE leads SET owner_name = 'Web Person', owner_source = 'Website team page' WHERE place_id = 'd'")
con.commit()

answers = {"/search/companies": {"leo newman": {"items": [active("LEO NEWMAN LTD", "111")]},
                                 "marsh parsons london bridge estate agents": {"items": [active("MARSH & PARSONS LIMITED", "222")]}},
           "/company/111/persons-with-significant-control": psc,
           "/company/222/persons-with-significant-control": {"items": [{"kind": "corporate-entity-person-with-significant-control"}]},
           "/company/222/officers": {"items": [{"name": "BLOGGS, Joe", "officer_role": "director"}]}}
asked = []


def handler(request):
    asked.append(request.url.path)
    assert request.headers["authorization"].startswith("Basic ")
    got = answers.get(request.url.path)
    if request.url.path == "/search/companies":
        got = got.get(request.url.params["q"], {"items": []})
    return httpx.Response(200, json=got) if got is not None else httpx.Response(404, json={})


client = httpx.Client(transport=httpx.MockTransport(handler), base_url=ch.API)
assert ch.lookup_pending(con, client, 10, threading.Event(), key=None)["skipped"] == "no COMPANIES_HOUSE_KEY"
stats = ch.lookup_pending(con, client, 10, threading.Event(), key="k")
assert stats == {"looked_up": 2, "names": 2, "skipped": None}, stats
rows = {r[0]: r[1:] for r in con.execute("SELECT place_id, owner_name, owner_role, owner_source, ch_company FROM leads")}
assert rows["a"] == ("Leo Newman", "Owner", "Companies House owner", "111"), rows["a"]
assert rows["b"] == ("Joe Bloggs", "Director", "Companies House director", "222"), rows["b"]
assert rows["c"][0] is None and rows["d"][0] == "Web Person"  # AE skipped; a website name outranks the register
assert not any("Nowhere" in a for a in asked)
assert ch.lookup_pending(con, client, 10, threading.Event(), key="k")["looked_up"] == 0  # each lead once

# a rejected key or the rate limit ends the cycle; the lead stays unchecked for next time
lead("e", "Fresh Estates")
con.commit()
for code, why in ((401, "Companies House key rejected"), (429, "Companies House rate limit")):
    limited = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(code)), base_url=ch.API)
    assert ch.lookup_pending(con, limited, 10, threading.Event(), key="k")["skipped"] == why
assert con.execute("SELECT ch_checked_at FROM leads WHERE place_id = 'e'").fetchone()[0] is None
print("all checks passed")
