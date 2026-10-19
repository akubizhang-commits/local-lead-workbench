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
