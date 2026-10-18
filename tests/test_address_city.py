"""Actual city parsed from the address (design §5.1)."""
import pytest

from leadworkbench.address_city import city_from_address


@pytest.mark.parametrize("address,country,city", [
    ("100 Winchester Blvd, Campbell, CA 95008", "US", "Campbell"),
    ("100 Winchester Blvd, Suite 200, San Jose, CA 95128-1234, United States", "US", "San Jose"),
    ("12 Main Rd, Sandton, Johannesburg, 2196, South Africa", "ZA", "Johannesburg"),
    ("1 Main St, Suite 5, CA 95008", "US", None),
    ("somewhere unparsable", "US", None),
    ("", "US", None),
    ("100 Main St, Campbell, CA 95008", "GB", None),
])
def test_city_from_address(address, country, city):
    assert city_from_address(address, country) == city
