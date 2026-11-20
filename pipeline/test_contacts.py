"""Self-check for website contact extraction. Run: python pipeline/test_contacts.py"""
import contacts as c


def page(body, head=""):
    return c.parse_page(f"<html><head>{head}</head><body>{body}</body></html>")


# emails, with the person's name when the page attaches one
assert c.emails_in(page('<a href="mailto:jane@smile.ae">Jane Smith</a>')) == {"jane@smile.ae": "Jane Smith"}
assert c.emails_in(page("Write to Jane Smith &lt;jane@smile.ae&gt; today")) == {"jane@smile.ae": "Jane Smith"}
assert c.emails_in(page("jane [at] smile [dot] ae")) == {"jane@smile.ae": None}
assert c.emails_in(page("jane (at) smile.ae")) == {"jane@smile.ae": None}
assert c.emails_in(page("meet us at clinic dot com")) == {}                      # plain words are not an email
assert c.emails_in(page('<a href="mailto:info@smile.ae">SMILE DENTAL LTD</a>')) == {"info@smile.ae": None}
assert c.emails_in(page('<a href="mailto:info@smile.ae">Email us</a>')) == {"info@smile.ae": None}
assert c.emails_in(page('<a href="mailto:Info@Smile.ae?subject=Hi">info@smile.ae</a>')) == {"info@smile.ae": None}

# owners from structured data
ld = '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Dentist","name":"Smile","founder":{"@type":"Person","name":"Sara Ali"}}]}</script>'
bad = '<script type="application/ld+json">{not json</script>'
p1 = page("", ld + bad)
assert c.owner_from_json_ld(c.json_ld(p1), "Smile") == ("Sara Ali", "Founder")
ceo = '<script type="application/ld+json">[{"@type":"Person","name":"Omar Khan","jobTitle":"Chief Executive Officer (CEO)"}]</script>'
assert c.owner_from_json_ld(c.json_ld(page("", ceo)), "Smile") == ("Omar Khan", "CEO")
assert c.owner_from_json_ld([{"@type": "Organization", "founder": "Smile Dental Group"}], "Smile") is None
# live run: some sites give jobTitle as a list (or junk); that must not crash the crawl
assert c.owner_from_json_ld([{"@type": "Person", "name": "Omar Khan", "jobTitle": ["Dentist", "Founder"]}], "Smile") \
    == ("Omar Khan", "Founder")
assert c.owner_from_json_ld([{"@type": "Person", "name": "Omar Khan", "jobTitle": {"x": 1}}], "Smile") is None
# live run: greetings, departments and hospital names were taken as owners
for junk in ("Hi Karen", "Medcare Hospitals", "Special Projects English", "Dear Patient",
             "General Dentist", "Principal Dentist", "Senior Orthodontist", "Managing Partner"):
    assert c.clean_person(junk) is None, junk
assert c.clean_person("Karen Lee") == "Karen Lee"

# owners from team-page text: highest role wins; a tie at the top is ambiguous -> nothing
assert c.owner_from_text("Dr. Omar Khan, Founder. Lina Haddad, Director.", "Smile") == ("Dr. Omar Khan", "Founder")
assert c.owner_from_text("Managing Director: Priya Nair", "Smile") == ("Priya Nair", "Managing Director")
assert c.owner_from_text("John Doe, Director. Mary Roe, Director.", "Smile") is None
assert c.owner_from_text("Meet Our Team, Director of nothing", "Smile") is None
assert c.owner_from_text("مدير العيادة: سارة علي", "عيادة") is None                   # non-Latin text: no crash

# company-looking names are never owners
assert c.looks_like_company("Smile Dental Group", "x")
assert c.looks_like_company("ABC TRADING", "x")
assert c.looks_like_company("عيادة الابتسامة", "عيادة  الابتسامة")
assert not c.looks_like_company("Sara Ali", "Smile Clinic")

# which pages to read next
home = page('<a href="/contact-us">Contact</a><a href="/about">About us</a><a href="/blog/post-1">News</a>'
            '<a href="https://other.com/contact">Partner</a><a href="mailto:x@y.z">Mail</a>'
            '<a href="team.html">Our team</a><a href="tel:+97145662262">Call</a>',
            '<base href="https://smile.ae/en/">')
assert set(c.candidate_links(home, "https://www.smile.ae/")) == {
    "https://smile.ae/contact-us", "https://smile.ae/about", "https://smile.ae/en/team.html"}

# social links and sites that aren't the business's own
assert c.social_links(page('<a href="https://instagram.com/smile">IG</a><a href="/x">x</a>'), "https://smile.ae") == ["https://instagram.com/smile"]
assert c.is_aggregator("https://m.facebook.com/smile")
assert c.is_aggregator("https://www.google.ae/maps/place/x")
assert not c.is_aggregator("https://smile.ae")
assert all(c.is_aggregator(u) for u in ("https://wa.me/971501234567", "https://maps.app.goo.gl/x", "https://bit.ly/x",
                                        "https://api.whatsapp.com/send?phone=1", "https://t.me/x"))
assert c.is_blocked_domain("https://dha.gov.ae", [".gov.ae"])
assert c.is_blocked_domain("https://www.nhs.uk", [".nhs.uk"])
assert not c.is_blocked_domain("https://smile.ae", c.load_blocked_domains())
assert ".gov.uk" in c.load_blocked_domains()

# authenticity: the site shows the listing's phone or name
assert c.site_matches_listing("Call 04 566 2262", "+971 4 566 2262", "x")
assert c.site_matches_listing("Welcome to  SMILE clinic", None, "Smile Clinic")
assert not c.site_matches_listing("Hello", "+971 4 566 2262", "Smile Clinic")

# challenge pages are skipped, never mined; a normal page with a Turnstile form is not a block
assert c.is_block_page(403, "<html>...captcha-delivery.com...</html>")
assert c.is_block_page(200, "<html><body><div id='cf-chl-widget'></div></body></html>")
assert not c.is_block_page(200, "<html><body>" + "real text " * 20 + "</body></html>")
assert not c.is_block_page(200, "<html><body>" + "contact us " * 20 +
                           "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script></body></html>")
assert c.is_block_page(503, "<html><body>Checking your browser <script src='https://challenges.cloudflare.com/x'></script></body></html>")
# a Cloudflare challenge that carries only its _cf_chl_opt script is still a challenge
assert c.is_block_page(200, "<html><script>window._cf_chl_opt={cvId:'3'}</script>" + "please wait " * 10 + "</html>")
# an empty JavaScript shell is not a block: the crawl labels it "empty"
assert not c.is_block_page(200, "<html><body><div id='root'></div></body></html>")

# Google Sites is a business's own website builder, not a Google page
assert not c.is_aggregator("https://sites.google.com/view/smileclinic")
assert c.is_aggregator("https://www.google.com/maps/place/x")

# long unbroken text (minified pages) must not make the email patterns backtrack for minutes
import time
t = time.time()
assert c.emails_in(page("x" * 200_000 + " end@smile.ae")) == {"end@smile.ae": None}
assert time.time() - t < 3, time.time() - t

assert c.phones_in(page('<a href="tel:+97145662262">Call</a>'), "AE") == ["+97145662262"]
assert c.phones_in(page("Mobile: 050 123 4567"), "AE") == ["+971501234567"]

# accented and Mc names: the whole name or none, never the tail of one
assert c.owner_from_text("Zoë Anne Smith, Founder", "Smile Clinic")[0] == "Zoë Anne Smith"
assert c.owner_from_text("Chloë Grace Martin, CEO", "Smile Clinic")[0] == "Chloë Grace Martin"
assert "Renée" in c.owner_from_text("Dr. Renée Marie Dubois - Owner", "Smile Clinic")[0]
assert (c.owner_from_text("Mary McKenzie Ross, Founder", "Smile Clinic") or [None])[0] != "Kenzie Ross"

# live pages with no name: what each one actually said
tara = "Williams Referred Urban Realty Listing Agent Tara Lee Crooks Managing Partner & CEO of Getting It Done"
assert c.owner_from_text(tara, "The Christine Cowern Real Estate Team") == ("Tara Lee Crooks", "Managing Partner")
brel = "Sign Up Brendan Powell - Broker of Record/Owner Melanie Piche - Broker/Owner Office: 416-274-2068"
assert c.owner_from_text(brel, "the BREL team East") == ("Brendan Powell & Melanie Piche", "Owner")
assert c.owner_from_text("Ana Bo, Owner. Cyd Do, Owner. Eda Fu, Owner.", "X") is None  # three owners: unclear
# a plain space before a title only counts for a capitalized senior title, never prose or a bare "director"
assert c.owner_from_text("Property Management Overseas owner reporting", "District") is None
assert c.owner_from_text("Lina Haddad Director of Marketing", "Smile") is None
assert c.owner_from_text("Sara Ali Founder", "Smile") == ("Sara Ali", "Founder")
# second live run: a heading word before the name is dropped; branch and badge text is no person
assert c.owner_from_text("Staff Calum White - Founder", "White & Co") == ("Calum White", "Founder")
assert c.owner_from_text("Directors Anita Mehra - Managing Director", "Benham") == ("Anita Mehra", "Managing Director")
assert c.owner_from_text("Founders Hanane Dawson, Founder. Chris Dawson, Founder", "Dolce Vita") == \
    ("Hanane Dawson & Chris Dawson", "Founder")
assert c.owner_from_text("Business Bay Branch - CEO", "Provident") is None
assert c.owner_from_text("Best Seller Owner", "GLP") is None
# the "Dr" the site names is the one in the business name
assert c.owner_in_business("Meet Dr Zeinab Taie and Dr. Ali Omar", "Dentist Zeinab Taie (Dubai - Marina)") == \
    ("Dr Zeinab Taie", "Owner")
assert c.owner_in_business("Dr Evelien Zijp, Dr Gina Rau", "Sky Clinic Dental Center") is None
print("all checks passed")
