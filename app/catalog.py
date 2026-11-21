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

# Courier pricing (GH₵). Halls/on-campus places: 2-5. Campus hostels: 5-10. Anything else has no courier service.
HOSTEL_NAMES = {h.lower() for h in HOSTELS}
HALL_NAMES = {h.lower() for h in HALLS + LEGACY_CAMPUS}
OFFER_MIN_STEP = 0.5
OFFER_TTL_MINUTES = 15
FIXED_ACCEPT_AFTER_MINUTES = 10
MAX_OPEN_OFFERS = 5


def courier_price_range(pickup: str | None, dropoff: str | None) -> tuple[float, float] | None:
    """Allowed courier price range for a trip, or None if either end is off campus (no courier service)."""
    ends = [(place or "").strip().lower() for place in (pickup, dropoff)]
    if any(e not in HOSTEL_NAMES and e not in HALL_NAMES for e in ends):
        return None
    return (5.0, 10.0) if any(e in HOSTEL_NAMES for e in ends) else (2.0, 5.0)