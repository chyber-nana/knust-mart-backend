CATEGORIES = ["Textbooks", "Electronics", "Food & Snacks", "Room Essentials", "Fashion", "Services", "Other"]
CONDITIONS = ["New", "Like new", "Used - good", "Used - fair"]
HALLS = ["Africa", "Republic", "Unity", "Katanga", "Queens", "Independence"]
HOSTELS = ["Wilkado Hostel", "SRC Hostel", "Brunei Hostel", "GUSSS Hostel"]
LOCATIONS = HALLS + HOSTELS
# Names used by older listings; the "Off campus" filter still treats them as campus.
LEGACY_CAMPUS = ["Evandy", "Queen Elizabeth II", "Main Library", "Student Union Building"]
CAMPUS_PLACES = LOCATIONS + LEGACY_CAMPUS
OFF_CAMPUS = "Off campus"
MAX_LOCATION_LEN = 80


def clean_location(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split())[:MAX_LOCATION_LEN]
    return cleaned or None


FEES = {"student": {"listing": 0, "promotion": 5}, "external": {"listing": 0, "promotion": 5}}
DELIVERY_FEE = 5