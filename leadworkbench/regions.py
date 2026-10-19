"""Local geographic reference data. Places and counties are not a strict tree."""
from functools import lru_cache
import gzip
import json
from pathlib import Path
from .errors import Invalid


DATASETS = Path(__file__).parent / "datasets"


@lru_cache(maxsize=1)
def country_configs():
    """Supported selector paths, labels and catalogue locations, independent of UI."""
    return json.loads((DATASETS / "region-configs.json").read_text())


def country_config(country):
    return next((c for c in country_configs() if c["country"] == country), None)


@lru_cache(maxsize=8)
def catalogue(country="US"):
    config = country_config(country)
    if not config:
        raise Invalid("该国家尚未收录地区目录")
    with gzip.open(DATASETS / config["dataset"], "rt", encoding="utf-8") as stream:
        return json.load(stream)


@lru_cache(maxsize=1)
def index():
    return {row["id"]: row for config in country_configs()
            for row in catalogue(config["country"])["regions"]}


@lru_cache(maxsize=1)
def named_places():
    names = {}
    for row in index().values():
        if row["kind"] == "place":
            for name in {row["name"], row["official_name"]}:
                names.setdefault((row["state_id"], name.casefold()), []).append(row["id"])
    return names


def unique_place_id(country, state, city):
    country = str(country).upper()
    if not country_config(country):
        return None
    state = str(state).strip().casefold()
    states = [r for r in regions(country) if state in
              {r["name"].casefold(), r["id"].removeprefix(country + "-").casefold()}]
    if len(states) != 1:
        return None
    matches = named_places().get((states[0]["id"], str(city).strip().casefold()), [])
    return matches[0] if len(matches) == 1 else None


def regions(parent_id=None, kind=None, county_id=None):
    if county_id:
        county = index().get(county_id)
        if not county or county["kind"] != "county" or county["state_id"] != parent_id:
            raise Invalid("县与所选州不匹配")
    rows = [r for r in index().values() if r["parent_id"] == parent_id]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    elif parent_id and not country_config(parent_id):
        # Older consumers request state children as cities.
        rows = [r for r in rows if r["kind"] == "place"]
    if county_id:
        rows = [r for r in rows if county_id in r.get("county_ids", [])]
    return rows


def validate_selection(payload):
    """Validate IDs against the catalogue, retain manual text for other countries."""
    ids = {key: payload.get(key) for key in ("state_id", "county_id", "place_id")}
    if not any(ids.values()):
        if payload.get("county"):
            raise Invalid("请选择有效的县，或清空县筛选")
        return payload
    config = country_config(payload.get("country"))
    if not config:
        raise Invalid("地区编码与国家不匹配")
    state = index().get(ids["state_id"])
    if not state or state["kind"] != "state" or state["parent_id"] != payload.get("country"):
        raise Invalid("请选择有效的州")
    if payload.get("state") != state["name"]:
        raise Invalid("州名称与地区编码不一致，请重新选择")
    county = index().get(ids["county_id"]) if ids["county_id"] else None
    if ids["county_id"] and (not county or county["kind"] != "county" or county["state_id"] != state["id"]):
        raise Invalid("县与所选州不匹配")
    if county and payload.get("county", county["name"]) != county["name"]:
        raise Invalid("县名称与地区编码不一致")
    place = index().get(ids["place_id"]) if ids["place_id"] else None
    if ids["place_id"] and (not place or place["kind"] != "place" or place["state_id"] != state["id"]):
        raise Invalid("城市与所选州不匹配")
    if place and payload.get("city") not in (place["name"], place["official_name"]):
        raise Invalid("城市名称与地区编码不一致，请重新选择")
    if place and county and county["id"] not in place["county_ids"]:
        raise Invalid("城市与所选县不匹配")
    if county and not place:
        raise Invalid("按县筛选时，请从城市列表中选择有效城市")
    if config["mode"] == "municipal_branch":
        if not place:
            raise Invalid("请选择大都市自治市或地方自治市")
        if place["subtype"] == "local_municipality" and not county:
            raise Invalid("请选择地方自治市所属地区")
        if place["subtype"] == "metropolitan_municipality" and county:
            raise Invalid("大都市自治市不隶属于地区自治市")
    return {**payload, "county": county["name"] if county else "",
            "geography": {
                "state_id": state["id"], "county_id": county["id"] if county else None,
                "place_id": place["id"] if place else None,
                "county": county["name"] if county else "",
                "place_type": place["subtype"] if place else None,
                "official_name": place["official_name"] if place else None,
                "county_ids": place["county_ids"] if place else [],
                "vintage": catalogue(payload["country"])["metadata"]["vintage"],
                "country": payload["country"],
                "scope": config["scope"], "verified_selection": bool(place),
            }}
