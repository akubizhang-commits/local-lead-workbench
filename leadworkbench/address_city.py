"""Actual city from a Maps address, for exports only (design §5.1)."""
import re

US_STATE_ZIP = re.compile(r"^[A-Z]{2}\s+\d{5}(?:-\d{4})?$")
ZA_POSTCODE = re.compile(r"^\d{4}$")


def city_from_address(address, country):
    """City segment before "CA 95112" (US) or before a 4-digit postcode (ZA); None if not parsable."""
    parts = [part.strip() for part in str(address or "").split(",")]
    country = str(country or "").strip().upper()
    pattern = US_STATE_ZIP if country == "US" else ZA_POSTCODE if country == "ZA" else None
    if pattern is None:
        return None
    for index, part in enumerate(parts):
        if index and pattern.match(part):
            city = parts[index - 1].strip()
            if city and len(city) <= 80 and not re.search(r"\d", city):
                return city
            return None
    return None
