"""Self-check for LinkedIn names via Brave search results, against a fake Brave. Run: python pipeline/test_linkedin.py"""
import json
import os
import threading

import httpx

import linkedin as l
import process as p

l.time.sleep = lambda s: None  # the 1.1 s between calls is not slept in tests

# titles and picking
assert l.parse_title("Sara Ali - Founder - Smile Clinic | LinkedIn") == ("Sara Ali", "Founder")
assert l.query_for("Smile Clinic", "Dubai") == '"Smile Clinic" Dubai site:linkedin.com'
other = {"url": "https://ae.linkedin.com/in/omar", "title": "Omar Khan - CEO - Other Co | LinkedIn", "description": ""}
ours = {"url": "https://ae.linkedin.com/in/sara", "title": "Sara Ali - Founder - Smile Clinic | LinkedIn", "description": ""}
page = {"url": "https://www.linkedin.com/company/smile-clinic", "title": "Smile Clinic | LinkedIn", "description": ""}
post = {"url": "https://www.linkedin.com/posts/smile-x", "title": "Smile Clinic post", "description": ""}
assert l.pick([other], "Smile Clinic") is None                      # doesn't mention the business
assert l.pick([page, ours], "Smile Clinic") == ("https://ae.linkedin.com/in/sara", "Sara Ali", "Founder")
assert l.pick([post, page], "Smile Clinic") == ("https://www.linkedin.com/company/smile-clinic", None, None)
assert l.pick([{**ours, "title": "SMILE CLINIC LLC - Owner - Smile Clinic"}], "Smile Clinic") \
    == ("https://ae.linkedin.com/in/sara", None, None)                  # a company "name" gives the URL only


def fresh_db(owner_source=None):
    con = p.db(":memory:")
    for i in range(3):
        con.execute("""INSERT INTO leads(place_id, name, email, email_status, phone_e164, reviews, query, raw,
                       owner_name, owner_source) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (f"id{i}", "Smile Clinic", f"info{i}@smile.ae", "mx_ok", None, 50 - i, "AE|dentist|Dubai",
                     json.dumps({"title": "Smile Clinic"}), owner_source and "Lina Haddad", owner_source))
    return con


def brave(responses):
    calls = []

    def handler(request):
        calls.append(request)
        return responses.pop(0) if len(responses) > 1 else responses[0]
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


FOUND = httpx.Response(200, json={"web": {"results": [ours]}})

# no key: nothing is sent
os.environ.pop("BRAVE_API_KEY", None)
client, calls = brave([FOUND])
assert l.lookup_pending(fresh_db(), client, 10, threading.Event())["skipped"] == "no BRAVE_API_KEY" and not calls

os.environ["BRAVE_API_KEY"] = "test-key"

# the monthly cap: 3 eligible leads, cap 2 -> exactly 2 calls, usage 2
l.MONTHLY_CAP = 2
con = fresh_db()
client, calls = brave([FOUND])
stats = l.lookup_pending(con, client, 10, threading.Event())
assert len(calls) == 2 and l.usage(con) == (2, 2) and stats["looked_up"] == 2 and stats["names"] == 2, stats
assert calls[0].headers["X-Subscription-Token"] == "test-key" and calls[0].url.params["count"] == "5"
assert con.execute("SELECT owner_name, owner_source, linkedin_url FROM leads WHERE place_id='id0'").fetchone() \
    == ("Sara Ali", "LinkedIn search result", "https://ae.linkedin.com/in/sara")
l.MONTHLY_CAP = 1000

# 402 (credit used up): stop after one call, nothing counted, the lead stays unchecked
con = fresh_db()
client, calls = brave([httpx.Response(402)])
stats = l.lookup_pending(con, client, 10, threading.Event())
assert len(calls) == 1 and l.usage(con)[0] == 0 and stats["skipped"] == "brave limit", stats
assert con.execute("SELECT COUNT(*) FROM leads WHERE linkedin_checked_at IS NULL").fetchone()[0] == 3

# a 200 with no match still marks the lead checked, so it is never searched again
con = fresh_db()
client, calls = brave([httpx.Response(200, json={"web": {"results": [other]}})])
l.lookup_pending(con, client, 1, threading.Event())
assert con.execute("SELECT linkedin_checked_at IS NOT NULL, linkedin_url FROM leads WHERE place_id='id0'").fetchone() == (1, None)

# an owner already found on the website is never looked up
client, calls = brave([FOUND])
assert l.lookup_pending(fresh_db("Website team page"), client, 10, threading.Event())["looked_up"] == 0 and not calls
print("all checks passed")
