from leadworkbench.contact_fields import contact_fields


def test_person_fields_from_explicit_labels():
    contact = dict(name="Fixture Person", role="Sales Manager", email="person@example.com",
                   source_url="https://example.com/team", association_status="same_block",
                   evidence="Fixture Person Sales Manager Language: English & Spanish Extension: 108 "
                   "Cell / WhatsApp: (213) 454-3667 Email: person@example.com")
    rows = contact_fields(contact, "https://www.example.com/")
    values = {r["key"]: r["value"] for r in rows}
    assert values == dict(email="person@example.com", phone="2134543667", extension="108",
                          role="Sales Manager（销售经理）", whatsapp="2134543667",
                          language="English & Spanish")
    assert all(r["source_label"] == "企业官网" for r in rows)
    assert all(r["source_url"] == contact["source_url"] for r in rows)


def test_shared_mailbox_does_not_inherit_person_fields():
    rows = contact_fields(dict(email="info@example.com", association_status="company_only",
                               evidence="Sales Manager Phone: (213) 454-3667 Language: English"),
                          "https://example.com")
    assert [r["key"] for r in rows] == ["email"]


def test_source_is_per_record_and_missing_fields_are_omitted():
    rows = contact_fields(dict(name="Fixture Person", role="Buyer", source_url="https://www.linkedin.com/in/fixture",
                               association_status="same_block", evidence="Buyer"))
    assert rows[0]["value"] == "Buyer（采购员）"
    assert rows[0]["source_label"] == "社媒 · LinkedIn"
    assert len(rows) == 1


def test_no_whatsapp_from_unlabelled_phone_or_extension_inference():
    rows = contact_fields(dict(name="Fixture Person", association_status="same_block",
                               evidence="Phone: +1 (213) 454-3667 | Extension: 108 | Email: a@example.com"))
    values = {r["key"]: r["value"] for r in rows}
    assert values == {"phone": "+12134543667", "extension": "108"}


def test_complete_language_label_wins_over_truncated_evidence():
    rows = contact_fields(dict(name="Fixture Person", association_status="same_block",
                               evidence="Language: English & Spanish Extensi | "
                               "Language: English & Spanish Extension: 108"))
    assert next(r["value"] for r in rows if r["key"] == "language") == "English & Spanish"
