CATEGORIES = ["Textbooks", "Electronics", "Food & Snacks", "Room Essentials", "Fashion", "Services", "Other"]
CONDITIONS = ["New", "Like new", "Used - good", "Used - fair"]
# Same names deliveries.py accepts, so a product's location can be a delivery pickup.
LOCATIONS = ["Unity", "Independence", "Evandy", "Republic", "Queen Elizabeth II", "Africa", "Off-Campus Commercial Hub", "Main Library", "Student Union Building"]
# Promotion rates in GH₵ (display only; promotion billing is not connected yet).
# Listings are free: businesses pay a one-off activation fee instead (see app/billing.py).
FEES = {"student": {"listing": 0, "promotion": 5}, "external": {"listing": 0, "promotion": 30}}
DELIVERY_FEE = 5
