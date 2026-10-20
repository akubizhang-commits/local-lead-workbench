"""Review noise over the 2026-10-17 real-evidence fixture (design §4.9)."""
import review_noise_measure as measure


def test_fixture_loads_and_measure_script_runs():
    fixture = measure.load_fixture()
    assert set(fixture["groups"]) == set(measure.GROUPS)
    assert {name: len(group["baseline"]) for name, group in fixture["groups"].items()} == {
        "product": 5, "qa07": 3, "qa09": 3}
    assert sum(len(g["baseline"]) for g in fixture["groups"].values()) == 11
    text = measure.report(fixture)
    assert "合计" in text and "市场路径" in text


def test_format_only_phone_is_not_reviewed_on_the_non_market_path():
    generation = measure.measure_generation()
    phones = {(name, r["company"], r["new_value"]) for name, g in generation.items()
              for r in g["reviews"] if r["field"] == "phone"}
    assert not any(company == "omni" for _, company, _ in phones)
    # A really different number still needs review.
    assert ("product", "superior", "4082950911") in phones
    assert ("qa09", "superior", "4082950911") in phones


def test_format_only_phone_is_not_rewritten_on_the_market_path():
    market = measure.measure_market_path()
    for name in ("product", "qa07"):
        assert not [r for r in market[name]["rewritten"] if (r["company"], r["field"]) == ("omni", "phone")]
        assert not [c for c in market[name]["changes"] if (c["company"], c["field"]) == ("omni", "phone")]
        assert "(408) 435-5020" in {o["value"] for o in market[name]["observations"]["omni"] if o["field"] == "phone"}


def _company(fixture, group, key):
    return next(c for c in fixture["groups"][group]["companies"] if c["key"] == key)


def test_generation_keeps_only_real_disagreements():
    generation = measure.measure_generation()
    assert {name: len(g["reviews"]) for name, g in generation.items()} == {"product": 1, "qa07": 0, "qa09": 2}
    kept = {(name, r["company"], r["field"], r["new_value"]) for name, g in generation.items() for r in g["reviews"]}
    assert kept == {("product", "superior", "phone", "4082950911"),
                    ("qa09", "superior", "phone", "4082950911"),
                    ("qa09", "superior", "name", "Superior Roof Metals")}
    for name in ("product", "qa07"):
        names = {o["value"] for o in generation[name]["observations"]["omni"] if o["field"] == "name"}
        assert names == {"Omni Sheet Metal"}  # Cleaned title, observation only.
    for name in ("product", "qa09"):
        superior = generation[name]["observations"]["superior"]
        assert not [o for o in superior if "manufacturer" in o["value"] and o["field"] == "business_types"]


def test_market_path_leaves_noise_fields_untouched():
    market = measure.measure_market_path()
    for name, m in market.items():
        assert m["noise_rewritten"] == [], name
        assert not [c for c in m["changes"] if (c["company"], c["field"]) in measure.NOISE_FIELDS], name
        assert set(m["changed"].values()) == {0}, name
    omni = {(o["field"], o["value"]) for o in market["product"]["observations"]["omni"]}
    assert {("name", "Omni Sheet Metal"), ("phone", "(408) 435-5020")} <= omni
    # Control group: a really different phone still follows the old logic.
    assert [(c["field"], c["new_value"], c["reason"]) for c in market["product"]["changes"]
            if c["company"] == "superior"] == [("phone", "4082950911", "市场来源资料更新")]
    assert market["qa09"]["reviews"] == 2  # Manually protected name and phone: reviewed, not overwritten.


def test_non_enum_business_types_only_become_evidence(tmp_path):
    fixture = measure.load_fixture()
    store = measure.Store(tmp_path)
    cid = measure.seed_company(store, _company(fixture, "product", "superior"))
    run = store.refresh(cid)
    page = {"field": "business_types", "value": ["manufacturer"], "source_url": "https://superiorroofmetals.com/",
            "evidence": "sheet metal manufacturing plant", "observed_at": "2026-10-17T08:30:00+00:00"}
    store.apply_enrichment(cid, {"status": "completed", "observations": [page], "contacts": []}, run_id=run["id"])
    with store.connect() as con:
        assert con.execute("SELECT business_types FROM companies WHERE id=?", (cid,)).fetchone()[0] == '["retailer"]'
        assert con.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
        assert [tuple(r) for r in con.execute("SELECT field,value FROM observations WHERE company_id=? AND method='website'",
                                              (cid,))] == [("business_types_evidence", '["manufacturer"]')]


def test_parser_cleans_names_and_splits_business_types():
    from leadworkbench.sources.website import parse_page
    fixture = measure.load_fixture()
    omni = _company(fixture, "product", "omni")["pages"][0]
    names = [o for o in parse_page(omni["html"], omni["url"])["observations"] if o["field"] == "name"]
    assert [(o["value"], o["name_source"]) for o in names] == [("Omni Sheet Metal", "title")]
    assert names[0]["evidence"].startswith("title: ") and measure_title(omni["html"]) in names[0]["evidence"]
    careers = _company(fixture, "product", "superior")["pages"][1]
    assert careers["url"].endswith("/careers/")
    assert not [o for o in parse_page(careers["html"], careers["url"])["observations"]
                if o["field"].startswith("business_types")]
    about = parse_page(careers["html"], "https://superiorroofmetals.com/about/")["observations"]
    assert [(o["field"], o["value"]) for o in about if o["field"].startswith("business_types")] == [
        ("business_types_evidence", ["manufacturer"])]
    slogan = parse_page("<html><head><title>Welcome</title></head><body></body></html>", "https://a.example/")
    assert not [o for o in slogan["observations"] if o["field"] == "name"]


def measure_title(html):
    return html.split("<title>")[1].split("</title>")[0]


def test_blank_business_types_are_auto_filled_without_review(tmp_path):
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    fixture = measure.load_fixture()
    superior = dict(_company(fixture, "qa09", "superior"), business_types=[], overrides=[])
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    store = app.state.store
    cid = measure.seed_company(store, superior)
    run = store.refresh(cid)
    store.apply_enrichment(cid, measure.page_payload({**superior, "pages": superior["pages"][:1]}), run_id=run["id"])
    with store.connect() as con:
        assert con.execute("SELECT business_types FROM companies WHERE id=?", (cid,)).fetchone()[0] == '["retailer"]'
        assert not con.execute("SELECT 1 FROM reviews WHERE field='business_types'").fetchone()
    with TestClient(app) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        rows = [c for c in client.get("/api/changes").json() if c["field"] == "business_types"]
        detail = client.get(f"/api/companies/{cid}").json()["changes"]
    assert [(c["old_value"], c["new_value"], c["reason"], c["auto_fill"]) for c in rows] == [
        ("[]", '["retailer"]', "自动补全：原值为空", True)]
    assert [c["auto_fill"] for c in detail if c["field"] == "business_types"] == [True]
