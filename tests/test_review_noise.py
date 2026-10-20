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


def _all_groups(store):
    fixture = measure.load_fixture()
    for name in measure.GROUPS:
        measure.seed_baseline(store, name, fixture)


def test_cleanup_closes_only_obvious_duplicates(tmp_path):
    import hashlib
    from db_hash import content_hash
    from fastapi.testclient import TestClient
    from leadworkbench.api import create_app
    app = create_app(tmp_path, allowed_hosts={"testserver"})
    store = app.state.store
    _all_groups(store)
    assert measure.pending_count(store) == 11
    untouched = ["companies", "changes", "observations"]
    before = content_hash(store.path, tables=untouched)
    with store.connect() as con:
        total_reviews = con.execute("SELECT count(*) FROM reviews").fetchone()[0]
    with TestClient(app) as client:
        client.headers["X-Local-Token"] = client.get("/api/session").json()["token"]
        preview = client.get("/api/review-cleanup").json()
        assert preview["total"] == 6
        assert {r["rule"]: r["count"] for r in preview["rules"]} == {
            "phone_format": 2, "name_title": 2, "business_type_enum": 2}
        assert all(len(r["samples"]) == r["count"] and r["label"] for r in preview["rules"])
        assert client.post("/api/review-cleanup", json={"plan_id": "x" * 64}).status_code == 422
        assert client.post("/api/review-cleanup", json={}).status_code == 422
        done = client.post("/api/review-cleanup", json={"plan_id": preview["plan_id"]})
        assert done.status_code == 200
        assert done.json() == {"closed": 6, "rules": {"phone_format": 2, "name_title": 2, "business_type_enum": 2}}
        assert measure.pending_count(store) == 5
        again = client.get("/api/review-cleanup").json()
        assert again["total"] == 0 and again["rules"] == []
        assert again["plan_id"] == hashlib.sha256(b"").hexdigest()
        repeat = client.post("/api/review-cleanup", json={"plan_id": preview["plan_id"]})
        assert repeat.status_code == 200 and repeat.json() == {"closed": 0, "rules": {}}
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM reviews").fetchone()[0] == total_reviews
        assert con.execute("SELECT count(*) FROM review_resolutions").fetchone()[0] == 6
        assert {tuple(r) for r in con.execute("SELECT previous_status,status,batch_id FROM review_resolutions")} == {
            ("pending", "auto_closed", preview["plan_id"])}
        closed = con.execute("SELECT evidence FROM reviews WHERE status='auto_closed'").fetchall()
        assert all(r[0].endswith("，未修改企业资料") and "；审核清理：" in r[0] for r in closed)
        kept = sorted((r["kind"], r["field"]) for r in con.execute("SELECT kind,field FROM reviews WHERE status='pending'"))
    assert kept == [("duplicate_candidate", "company"), ("duplicate_candidate", "company"),
                    ("field_update", "name"), ("field_update", "phone"), ("field_update", "phone")]
    assert content_hash(store.path, tables=untouched) == before


def test_cleanup_refuses_a_changed_plan_and_never_runs_on_startup(tmp_path):
    from leadworkbench.db import Store
    from leadworkbench.errors import DomainError
    import pytest
    store = Store(tmp_path)
    _all_groups(store)
    preview = store.review_cleanup_preview()
    company = store.manual_company("Noise Co", "https://noise.example")
    with store.connect() as con:
        con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,status,
            created_at,fingerprint) VALUES('late',?,'field_update','phone','+1 408-435-5020','(408) 435-5020',
            'https://noise.example/','x','pending','2026-10-18',?)""", (company["id"], "late-fingerprint"))
        # Stored value matches the review's old value (startup rebases stale old values).
        con.execute("UPDATE companies SET phone='+1 408-435-5020' WHERE id=?", (company["id"],))
    from db_hash import content_hash
    before = content_hash(store.path)
    with pytest.raises(DomainError) as error:
        store.review_cleanup(preview["plan_id"])
    assert (error.value.code, error.value.status) == ("plan_changed", 409)
    assert str(error.value) == "待审核内容已变化，请重新预览后再确认清理。"
    assert content_hash(store.path) == before
    assert Store(tmp_path).review_cleanup_preview()["total"] == 7
    assert measure.pending_count(Store(tmp_path)) == 12  # D3: restarting never cleans up by itself.


def test_cleanup_name_rule_skips_maps_sources(tmp_path):
    from leadworkbench.db import Store
    store = Store(tmp_path)
    company = store.manual_company("Omni Sheet Metal, Inc.", "https://omni.example")
    with store.connect() as con:
        for n, url in enumerate(("https://www.google.com/maps/place/omni", "https://omni.example/")):
            con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,status,
                created_at,fingerprint) VALUES(?,?,'field_update','name','Omni Sheet Metal, Inc.','Omni Sheet Metal',
                ?,'x','pending','2026-10-18',?)""", (f"r{n}", company["id"], url, f"f{n}"))
    preview = store.review_cleanup_preview()
    assert [s["review_id"] for r in preview["rules"] for s in r["samples"]] == ["r1"]
