"""Self-check for the parsing, dedupe and export rules. Run: python pipeline/test_process.py"""
import process as p

# phone normalization: local formats need the country hint, landlines are dropped
assert p.to_e164("050 123 4567", "AE") == "+971501234567"
assert p.to_e164("+44 7911 123456", None) == "+447911123456"
assert p.to_e164("04 123 4567", "AE") is None          # Dubai landline
assert p.to_e164("", "AE") is None and p.to_e164(None, None) is None

# search ids carry country, sector and location; old ids were just the country
assert p.split_input("AE|real estate agency|Dubai") == ("AE", "real estate agency", "Dubai")
assert p.split_input("AE") == ("AE", "", "")

# junk and malformed emails never reach DNS
assert p.verify_email("logo@2x.png") == "invalid"
assert p.verify_email("john@example.com") == "invalid"
assert p.verify_email("not-an-email") == "invalid"
assert p.verify_email("info@gmail.com") == "mx_ok"     # real MX lookup (needs internet)
assert p.verify_email("x@no-such-domain-zz9q.com") == "invalid"

# own-domain email wins over a free-mail one
assert p.best_email(["someone@gmail.com", "hello@google.com"], "https://www.google.com/")[0] == "hello@google.com"

# dedupe: same email or same phone across listings (chains) is kept once
rows = [{"email": "a@x.com", "phone_e164": "+971501234567", "phone": ""},
        {"email": "a@x.com", "phone_e164": None, "phone": "04 1"},          # same email
        {"email": None, "phone_e164": "+971501234567", "phone": ""},         # same mobile
        {"email": "b@y.com", "phone_e164": None, "phone": ""}]
assert p.dedupe_contacts(rows) == [rows[0], rows[3]]

# owner name: only from a "Dr." title or a personal-looking mailbox, never a generic one
assert p.owner_of({"title": "Dr. Paul's Dental Clinic Dubai"}, None) == ("Dr. Paul", "Business name")
assert p.owner_of({"title": "Smile Clinic"}, "sarah.jones@smileclinic.ae") == ("Sarah Jones", "Email address (guess)")
assert p.owner_of({"title": "Smile Clinic"}, "info@smileclinic.ae") == ("", "Not found")
assert p.owner_of({"title": "Smile Clinic"}, "smile@smileclinic.ae") == ("", "Not found")

# authenticity verdict
lead = {"website": "https://smile.ae", "reviews": 120, "email": "hi@smile.ae", "email_status": "mx_ok",
        "phone_e164": "+971501234567", "phone": "050 123 4567"}
assert p.verdict({"owner": {"name": "x"}}, lead).startswith("Verified (owner-claimed, 120 reviews, website, email on own domain")
assert p.verdict({"status": "Permanently closed"}, lead) == "Closed"
assert p.verdict({}, {**lead, "website": "", "reviews": 2, "email_status": None}) == "Weak (valid phone)"
print("all checks passed")
