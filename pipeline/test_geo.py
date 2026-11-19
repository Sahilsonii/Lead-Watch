"""Self-check for the "All of <country>" place list. Run: python pipeline/test_geo.py"""
import grid
import geo


def place(name, cc, admin1, pop, code="PPL", lat="26.0", lon="80.0", ascii=None):
    # GeoNames dump columns: id, name, ascii (2), alt, lat (4), lon (5), class, code (7), cc (8), cc2, admin1 (10), ..., population (14)
    return "\t".join(["1", name, name if ascii is None else ascii, "", lat, lon, "P", code, cc, "", admin1, "", "", "", str(pop), "", "", "", ""])


CITIES = "\n".join([
    place("Mumbai", "IN", "16", 12_000_000),
    place("Pune", "IN", "16", 3_000_000),
    place("Kanpur", "IN", "36", 2_900_000),
    place("Noida", "IN", "36", 600_000),
    place("Aurangabad", "IN", "16", 1_100_000),
    place("Aurangabad", "IN", "34", 50_000),        # same name, another state
    place("Gaya", "IN", "34", 20_000),
    place("Rampur", "IN", "36", 5_000),
    place("Rampur", "IN", "36", 3_000),            # same name, same state
    place("Lonely", "IN", "", 2_000),              # no state known
    place("Bandra", "IN", "16", 900_000, "PPLX"),  # neighborhood: skipped
    place("Vijayanagara", "IN", "19", 50_000, "PPLH"),  # historical place: skipped
    place("Dubai", "AE", "03", 3_000_000),         # other country: ignored
])
ADMIN1 = "IN.16\tMaharashtra\tMaharashtra\t1\nIN.36\tUttar Pradesh\tUttar Pradesh\t2\nIN.34\tBihar\tBihar\t3\n"
COUNTRIES = "#ISO\tISO3\tISO-Numeric\tfips\tCountry\nIN\tIND\t356\tIN\tIndia\tNew Delhi\nAE\tARE\t784\tAE\tUnited Arab Emirates\n"

places = geo.parse(CITIES, ADMIN1, COUNTRIES, "IN")
# smallest areas first: villages, towns, cities, states, country; least populated first inside each level
assert [(t["level"], t["location"]) for t in places] == [
    ("village", "Lonely"), ("village", "Rampur, Uttar Pradesh"), ("village", "Rampur, Uttar Pradesh 2"),
    ("town", "Gaya"), ("town", "Aurangabad, Bihar"),
    ("city", "Noida"), ("city", "Aurangabad, Maharashtra"), ("city", "Kanpur"), ("city", "Pune"), ("city", "Mumbai"),
    ("state", "Bihar"), ("state", "Uttar Pradesh"), ("state", "Maharashtra"),
    ("country", "India")], [(t["level"], t["location"]) for t in places]

village, town, city, state, country = places[1], places[3], places[5], places[10], places[-1]
assert village["n"] == 2 and village["of"] == 3 and village["state"] == "Uttar Pradesh"
assert city["n"] == 1 and city["of"] == 5 and city["bbox"] is None and city["query"] == "Noida, Uttar Pradesh"
assert state["bbox"] is None and state["query"] == "Bihar" and country["query"] == "India" and country["state"] == ""

# villages and towns get a box around GeoNames' point: no geocoding, so repeated names can't land on the wrong place
s, w, n, e = map(float, village["bbox"].split(","))
assert s < 26.0 < n and w < 80.0 < e
assert 15 < grid.bbox_area_km2((s, w, n, e)) < 17   # 4 km x 4 km: one grid cell
assert 63 < grid.bbox_area_km2(tuple(map(float, town["bbox"].split(",")))) < 65  # 8 km x 8 km

# "#" and "|" delimit gosom's input id, so they never reach a place name
odd = geo.parse(place("A#B|C", "IN", "16", 2_000), ADMIN1, COUNTRIES, "IN")
assert odd[0]["location"] == "A B C", odd
# the plain ASCII name is used ("Hatta", not "Ḩattā"), falling back to the native one when GeoNames has none
assert geo.parse(place("Ḩattā", "IN", "16", 2_000, ascii="Hatta"), ADMIN1, COUNTRIES, "IN")[0]["location"] == "Hatta"
assert geo.parse(place("Ḩattā", "IN", "16", 2_000, ascii=""), ADMIN1, COUNTRIES, "IN")[0]["location"] == "Ḩattā"

# a state named like a place gets its country, so the two never share a search key
clash = geo.parse(place("Dubai", "AE", "03", 3_000_000), "AE.03\tDubai\tDubai\t1\n", COUNTRIES, "AE")
assert [t["location"] for t in clash] == ["Dubai", "Dubai, United Arab Emirates", "United Arab Emirates"], clash
assert clash[1]["query"] == "Dubai, United Arab Emirates" and clash[1]["state"] == "Dubai"

# a country named like one of its places (Singapore) gets no separate country-wide search
sg = geo.parse(place("Singapore", "SG", "00", 5_000_000), "", "SG\tSGP\t702\tSN\tSingapore\n", "SG")
assert [t["location"] for t in sg] == ["Singapore"], sg

assert geo.parse(CITIES, ADMIN1, COUNTRIES, "ZZ") == []
assert geo.summary(places) == {"village": 3, "town": 2, "city": 5, "state": 3, "country": 1}
print("all checks passed")
