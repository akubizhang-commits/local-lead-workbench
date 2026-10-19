"""Pure review-noise rules (design §4.2)."""
import pytest

from leadworkbench import review_policy as policy


@pytest.mark.parametrize("value,country,expected", [
    ("(408) 435-5020", "US", ("14084355020", "")),
    ("+1 408-435-5020", "US", ("14084355020", "")),
    ("14084355020", "US", ("14084355020", "")),
    ("4082950911", "US", ("14082950911", "")),
    ("tel:4082950911", "US", ("14082950911", "")),
    ("tel: (408) 435-5020", "US", ("14084355020", "")),
    ("408-435-5020 ext. 12", "US", ("14084355020", "12")),
    ("+1 408 435 5020 x7", "US", ("14084355020", "7")),
    ("001 408 435 5020", "US", ("14084355020", "")),
    ("24084355020", "US", None),  # 11 digits not starting with 1
    ("435-5020", "US", None),
    ("021 555 1234", "ZA", ("27215551234", "")),
    ("27215551234", "ZA", ("27215551234", "")),
    ("+27 21 555 1234", "ZA", ("27215551234", "")),
    ("215551234", "ZA", ("27215551234", "")),
    ("0215551234", "GB", None),
    ("02079460958", None, None),
    ("+44 20 7946 0958", "GB", ("442079460958", "")),
    ("408-435-5020 call", "US", None),
    ("408+435+5020", "US", None),
    ("+1234567", "US", None),  # fewer than 8 digits after '+'
    ("", "US", None),
    (None, "US", None),
    ("４０８-４３５-５０２０", "US", ("14084355020", "")),  # NFKC full-width digits
])
def test_phone_key(value, country, expected):
    assert policy.phone_key(value, country) == expected


def test_phones_equivalent():
    assert policy.phones_equivalent("+1 408-435-5020", "(408) 435-5020", "US")
    assert not policy.phones_equivalent("+1 408-539-1935", "4082950911", "US")
    assert not policy.phones_equivalent("+1 408-000-0000", "4082950911", "US")
    # E.164 on one side, digits-only fallback on the other.
    assert policy.phones_equivalent("+44 20 7946 0958", "442079460958", "US")
    # The old digits-only rule still holds where the new key differs.
    assert policy.phones_equivalent("+4084355020", "4084355020", "US")
    # Without a country rule only the digits are compared.
    assert not policy.phones_equivalent("+1 408-435-5020", "(408) 435-5020", "GB")
    assert policy.phones_equivalent("021 555 1234", "+27 21 555 1234", "ZA")


def test_equivalent_dispatches_by_field():
    assert policy.equivalent("phone", "+1 408-435-5020", "(408) 435-5020", "US")
    assert policy.equivalent("address", "1 Main St", "1 Main St")
    assert not policy.equivalent("address", "1 Main St", "1 Main Street")
