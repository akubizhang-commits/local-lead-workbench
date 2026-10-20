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


OMNI_TITLE = "Omni Sheet Metal – Roofing, Panel Systems, Ornamental, Sun Control, Silicon Valley"


def test_required_assertions_from_the_evidence():
    assert policy.clean_site_name(OMNI_TITLE) == "Omni Sheet Metal"
    assert policy.website_name_verdict("Omni Sheet Metal, Inc.", OMNI_TITLE) == "equivalent"
    assert policy.website_name_verdict("Superior Roof Metals (人工修正)", "Superior Roof Metals") == "different"
    assert policy.business_types_add_nothing('["retailer"]', '["manufacturer"]') is True


@pytest.mark.parametrize("text,expected", [
    ("Home | Acme Supply", "Acme Supply"),
    ("Welcome", None),
    ("Welcome to Acme Supply", "Acme Supply"),
    ("Roofing, Siding, Gutters, Windows and Doors", None),
    ("Acme Supply - Shop", "Acme Supply"),
    ("首页 | 华美建材", "华美建材"),
    ("Contact Us.", None),
    ("  Acme   Supply  ", "Acme Supply"),
    ("A " * 11, None),
    ("x" * 81, None),
    (None, None),
])
def test_clean_site_name(text, expected):
    assert policy.clean_site_name(text) == expected


def test_name_verdict():
    assert policy.name_verdict("Omni Sheet Metal", "Omni Sheet Metal, Inc.") == "equivalent"
    assert policy.name_verdict("Acme L.L.C.", "Acme") == "equivalent"
    assert policy.name_verdict("Smith & Sons", "Smith and Sons") == "equivalent"
    # No word in common: a slogan, only stored as an observation (D6).
    assert policy.name_verdict("Omni Sheet Metal, Inc.", "Quality You Can Trust") == "unrelated"
    assert policy.website_name_verdict("Omni Sheet Metal, Inc.", "Home | Quality You Can Trust") == "unrelated"
    assert policy.name_verdict("Superior Roof Metals", "Superior Metals Supply") == "different"
    assert policy.name_verdict("Inc.", "Acme") == "different"  # Nothing left to compare: do not guess.


def test_enum_business_types():
    assert policy.enum_business_types('["retailer","manufacturer","Wholesale"]') == ["retailer", "wholesaler"]
    assert policy.enum_business_types(["import", "distributor"]) == ["importer"]
    assert policy.enum_business_types("not json") == []
    assert policy.enum_business_types(None) == []
    assert policy.business_types_add_nothing('["retailer"]', '["retailer"]') is True
    assert policy.business_types_add_nothing("broken", '["manufacturer"]') is True
    assert policy.business_types_add_nothing('["retailer"]', '["retailer","importer"]') is False
    assert policy.equivalent("business_types", '["retailer"]', '["retailer","manufacturer"]')


def test_blank_values_and_non_descriptive_paths():
    assert all(policy.is_blank("business_types", v) for v in (None, "", " ", "[]", "broken"))
    assert not policy.is_blank("business_types", '["retailer"]')
    assert not policy.is_blank("name", "Acme")
    for path in ("/careers/", "/jobs", "/blog/post-1", "/privacy", "/en/news/"):
        assert policy.NON_DESCRIPTIVE_PATH.search(path), path
    for path in ("/", "/products/", "/about-us/", "/jobsite-supplies/"):
        assert not policy.NON_DESCRIPTIVE_PATH.search(path), path


def test_auto_fill_detection():
    assert policy.is_auto_fill("[]", "补全空字段")
    assert policy.is_auto_fill(None, "自动补全旧空字段")
    assert policy.is_auto_fill("", policy.AUTO_FILL_REASON)
    assert not policy.is_auto_fill("", "人工修正")
    assert not policy.is_auto_fill('["retailer"]', policy.AUTO_FILL_REASON)
