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
