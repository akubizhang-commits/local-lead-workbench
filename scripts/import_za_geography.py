"""Import Treasury's municipal catalogue, without application/database imports.

Uses curl (including the caller's proxy environment) and the Python standard library.
Cached source responses are hash checked. --refresh explicitly replaces the snapshot.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://municipaldata.treasury.gov.za/api/cubes"
SOURCES = {
    "municipalities": f"{BASE}/municipalities/facts?pagesize=1000",
    "municipalities_model": f"{BASE}/municipalities/model",
    "demarcation_changes": f"{BASE}/demarcation_changes/facts?pagesize=1000",
    "demarcation_changes_model": f"{BASE}/demarcation_changes/model",
}
EXPECTED = {"states": 9, "counties": 44, "local_municipalities": 205,
            "metropolitan_municipalities": 8, "places": 213, "regions": 267}
SUFFIX = {"A": "Metropolitan Municipality", "B": "Local Municipality",
          "C": "District Municipality"}
SUBTYPE = {"A": "metropolitan_municipality", "B": "local_municipality",
           "C": "district_municipality"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def source_snapshot(directory, refresh=False):
    """Fetch complete API responses, or replay a verified cached snapshot."""
    manifest_path = directory / "manifest.json"
    if refresh or not manifest_path.exists():
        responses = {}
        for key, url in SOURCES.items():
            result = subprocess.run(
                ["curl", "--fail", "--silent", "--show-error", "--location",
                 "--proto", "=https", "--proto-redir", "=https",
                 "--connect-timeout", "15", "--max-time", "90",
                 "--retry", "3", url],
                check=True, capture_output=True,
            )
            json.loads(result.stdout)
            responses[key] = result.stdout
        manifest = {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "sources": {key: {"url": SOURCES[key], "sha256": hashlib.sha256(raw).hexdigest()}
                        for key, raw in responses.items()},
        }
        # Write manifest last: an interrupted refresh is detected by hash validation.
        for key, raw in responses.items():
            atomic_write(directory / f"{key}.json", raw)
        atomic_write(manifest_path, encode(manifest))
    manifest = json.loads(manifest_path.read_bytes())
    require(set(manifest["sources"]) == set(SOURCES), "Snapshot has missing/unknown sources")
    responses = {}
    for key, url in SOURCES.items():
        raw = (directory / f"{key}.json").read_bytes()
        require(manifest["sources"][key]["url"] == url, f"{key}: source URL mismatch")
        require(hashlib.sha256(raw).hexdigest() == manifest["sources"][key]["sha256"],
                f"{key}: cached source hash mismatch; inspect or explicitly --refresh")
        responses[key] = json.loads(raw)
    return responses, manifest


def facts(response, label):
    require(response.get("status") == "ok", f"{label}: API did not succeed")
    rows = response.get("data")
    require(isinstance(rows, list), f"{label}: missing fact array")
    require(len(rows) == response.get("total_fact_count"),
            f"{label}: incomplete API page; refuse a truncated catalogue")
    require(response.get("page") == 1, f"{label}: expected first page")
    return rows


def model_vintage(response, name):
    require(response.get("status") == "ok" and response.get("name") == name,
            f"{name}: invalid model response")
    value = response["model"].get("last_updated")
    require(isinstance(value, str) and value, f"{name}: source vintage missing")
    return value


def build(responses, manifest, as_of):
    rows = facts(responses["municipalities"], "municipalities")
    transitions = facts(responses["demarcation_changes"], "demarcation_changes")
    vintage = model_vintage(responses["municipalities_model"], "municipalities")
    transition_vintage = model_vintage(responses["demarcation_changes_model"], "demarcation_changes")
    require(date.fromisoformat(vintage[:10]) <= as_of, "Municipality source is newer than --as-of")
    removed = set()
    established = set()
    effective_dates = set()
    for change in transitions:
        effective = date.fromisoformat(change["date.date"])
        if effective > as_of:
            continue
        effective_dates.add(effective.isoformat())
        if change["old_code_transition.code"] == "disestablished":
            removed.add(change["old_demarcation.code"])
        if change["new_code_transition.code"] == "established":
            established.add(change["new_demarcation.code"])
    require(not (removed & established),
            "A code was both established and disestablished; review temporal history before importing")
    require(effective_dates, "No effective official demarcation changes")
    require(len({r["municipality.demarcation_code"] for r in rows}) == len(rows),
            "Duplicate source municipality codes")
    active = [r for r in rows if r["municipality.demarcation_code"] not in removed]
    province_names = {}
    lookup = {r["municipality.demarcation_code"]: r for r in active}
    for row in active:
        province = row["municipality.province_code"]
        name = row["municipality.province_name"]
        require(province and name, "Missing source province")
        require(province_names.setdefault(province, name) == name, "Inconsistent province names")
        require(row["municipality.category"] in SUFFIX, "Unknown municipality category")
    states = [{
        "id": f"ZA-{code}", "name": name, "official_name": name, "kind": "state",
        "subtype": "province", "parent_id": "ZA", "geoid": code,
    } for code, name in sorted(province_names.items())]
    counties, places = [], []
    for code, row in sorted(lookup.items()):
        category = row["municipality.category"]
        province = row["municipality.province_code"]
        parent = row["municipality.parent_code"]
        source_name = row["municipality.name"].strip()
        require(source_name, f"{code}: empty name")
        # Treasury supplies short names and category separately. Do not label a
        # municipality as a city: both display-name fields carry the category.
        name = source_name if source_name.endswith(SUFFIX[category]) else f"{source_name} {SUFFIX[category]}"
        region = {
            "id": f"ZA-{'COUNTY' if category == 'C' else 'PLACE'}-{code}",
            "name": name, "official_name": name, "source_name": source_name,
            "parent_id": f"ZA-{province}", "state_id": f"ZA-{province}",
            "kind": "county" if category == "C" else "place",
            "subtype": SUBTYPE[category], "geoid": code,
        }
        if category == "B":
            district = lookup.get(parent)
            require(district is not None and district["municipality.category"] == "C",
                    f"{code}: missing active district {parent}")
            require(district["municipality.province_code"] == province,
                    f"{code}: district crosses source province")
            region["county_ids"] = [f"ZA-COUNTY-{parent}"]
        else:
            require(parent == province, f"{code}: metro/district parent must be province")
            if category == "A":
                region["county_ids"] = []
        (counties if category == "C" else places).append(region)
    regions = [{"id": "ZA", "name": "South Africa", "official_name": "South Africa",
                "parent_id": None, "kind": "country"}, *states, *counties, *places]
    counts = {
        "states": len(states), "counties": len(counties), "places": len(places),
        "local_municipalities": sum(r["subtype"] == "local_municipality" for r in places),
        "metropolitan_municipalities": sum(r["subtype"] == "metropolitan_municipality" for r in places),
        "regions": len(regions),
    }
    output = {
        "metadata": {
            "country": "ZA", "catalogue_release": "2026", "as_of": as_of.isoformat(),
            "downloaded_at": manifest["retrieved_at"], "vintage": vintage,
            "demarcation_changes_vintage": transition_vintage,
            "demarcation_effective_dates": sorted(effective_dates),
            "source_fact_count": len(rows), "active_municipality_count": len(active),
            "excluded_disestablished_codes": sorted(set(lookup_from_rows(rows)) & removed),
            "counts": counts, "sources": manifest["sources"],
            "scope": "9 provinces; 44 district municipalities; 205 local and 8 metropolitan municipalities. Not a city/town/settlement catalogue.",
            "naming": "name = Treasury municipality.name + category suffix; official_name equals name. source_name preserves the exact trimmed Treasury short name. Labels are not an independent verification of legal styled names.",
            "province_codes": "Treasury codes preserved, including GT and KZ; not claimed to be current ISO 3166-2 suffixes.",
            "county_relation": "Local municipality parent_code from Treasury identifies its district; districts and all places have province parent_id. Metros have no district.",
            "geometry": "Omitted: these API facts do not supply boundary geometry, representative points or bbox.",
            "vintage_note": "2026 is the catalogue release, not a claim of 2026 municipal boundaries. Source last_updated and effective transition dates are recorded separately; later unreported legal/name changes are not inferred.",
        },
        "regions": regions,
    }
    validate(output)
    return output


def lookup_from_rows(rows):
    return {row["municipality.demarcation_code"] for row in rows}


def validate(output):
    rows = output["regions"]
    index = {r["id"]: r for r in rows}
    require(len(index) == len(rows), "Duplicate output IDs")
    require(output["metadata"]["counts"] == EXPECTED,
            f"Unexpected catalogue counts: {output['metadata']['counts']}; review changed source")
    actual = Counter(r["kind"] for r in rows)
    require(actual == {"country": 1, "state": 9, "county": 44, "place": 213}, "Output kinds/counts mismatch")
    require(Counter(r["subtype"] for r in rows if r["kind"] == "place") ==
            {"local_municipality": 205, "metropolitan_municipality": 8}, "Terminal subtypes mismatch")
    linked_districts = set()
    for row in rows:
        require(row["name"] == row["official_name"], "Display/official-name mismatch")
        require(not any(key in row for key in ("geo", "geom", "bbox")), "Unsourced geometry")
        if row["kind"] == "country":
            require(row["id"] == "ZA" and row["parent_id"] is None, "Invalid country")
        elif row["kind"] == "state":
            require(row["parent_id"] == "ZA", "Province parent mismatch")
        else:
            parent = index.get(row["parent_id"])
            require(parent is not None and parent["kind"] == "state", "Missing province parent")
            require(row["state_id"] == row["parent_id"], "state_id must match province parent")
            if row["kind"] == "place":
                ids = row["county_ids"]
                require(len(ids) == (1 if row["subtype"] == "local_municipality" else 0),
                        "Invalid municipality/district cardinality")
                for cid in ids:
                    district = index.get(cid)
                    require(district is not None and district["kind"] == "county" and
                            district["state_id"] == row["state_id"], "Invalid district relationship")
                    linked_districts.add(cid)
    require(linked_districts == {r["id"] for r in rows if r["kind"] == "county"},
            "A district has no local municipality")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(tempfile.gettempdir()) / "za-geography-2026")
    parser.add_argument("--output", type=Path, default=ROOT / "leadworkbench/datasets/za-geography-2026.json.gz")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 24))
    parser.add_argument("--refresh", action="store_true", help="Fetch a new official source snapshot")
    args = parser.parse_args()
    responses, manifest = source_snapshot(args.source_dir, args.refresh)
    output = build(responses, manifest, args.as_of)
    compressed = gzip.compress(encode(output), mtime=0)
    validate(json.loads(gzip.decompress(compressed)))
    atomic_write(args.output, compressed)
    print(json.dumps({"output": str(args.output), "counts": output["metadata"]["counts"],
                      "sha256": hashlib.sha256(compressed).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
