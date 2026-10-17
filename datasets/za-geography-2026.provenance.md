# South Africa municipal catalogue — 2026 release

Artifact: `datasets/za-geography-2026.json.gz`; importer: `scripts/import_za_geography.py`.
This is a **municipality catalogue**, not a list of cities, towns or settlements.
The application and frontend have not been changed to load it.

## Primary sources and vintage

Downloaded 2026-09-24 from South African National Treasury's Municipal Money API:

- [Municipalities facts](https://municipaldata.treasury.gov.za/api/cubes/municipalities/facts?pagesize=1000): 292 records.
- [Municipalities model](https://municipaldata.treasury.gov.za/api/cubes/municipalities/model): `last_updated = 2026-07-30T09:26` (timezone unspecified by source).
- [Demarcation changes facts](https://municipaldata.treasury.gov.za/api/cubes/demarcation_changes/facts?pagesize=1000): 42 transitions, effective 2016-08-03.
- [Demarcation changes model](https://municipaldata.treasury.gov.za/api/cubes/demarcation_changes/model): `last_updated = 2016-12`.

Official [API documentation](https://municipaldata.treasury.gov.za/docs) describes the data.
The Treasury-linked [project repository](https://github.com/OpenUpSA/municipal-data) identifies the joint Treasury/OpenUp project;
its [Geography model](https://github.com/OpenUpSA/municipal-data/blob/376d154a5bb8ce6fe532108ddc507ee582204548/scorecard/models/geography.py)
defines category A = metro, B = local, C = district. Repository/demo fixtures were **not** used as the catalogue: they are incomplete samples.

Filter: remove unique `old_demarcation.code` with `old_code_transition.code == "disestablished"` and effective date on/before 2026-09-24.
This excludes 35 source municipalities, leaving 257 active municipality records: 44 districts + 205 locals + 8 metros.
Nine province rows are derived from consistent source province-code/name pairs; the country row is added.
All 267 output IDs are unique. The full exclusion list, source URLs and raw-response SHA-256 hashes are in `metadata`.

`2026` labels this catalogue release, **not a newly surveyed boundary vintage**. The importer preserves the official dataset's published names and relationships; it does not infer changes absent from that source.
No geometry, point or bbox is emitted: these facts contain no verified boundary geometry/representative coordinates.
The misleading source measure `area` is not imported.

## Counts

| Province ID | Source province name | Districts | Locals | Metros |
|---|---|---:|---:|---:|
| ZA-EC | Eastern Cape | 6 | 31 | 2 |
| ZA-FS | Free State | 4 | 18 | 1 |
| ZA-GT | Gauteng | 2 | 6 | 3 |
| ZA-KZ | Kwazulu-Natal | 10 | 43 | 1 |
| ZA-LP | Limpopo | 5 | 22 | 0 |
| ZA-MP | Mpumalanga | 3 | 17 | 0 |
| ZA-NC | Northern Cape | 5 | 26 | 0 |
| ZA-NW | North West | 4 | 18 | 0 |
| ZA-WC | Western Cape | 5 | 24 | 1 |
| Total | 9 provinces | 44 | 205 | 8 |

Treasury's codes and spelling are retained, including `GT` and `KZ`; they are not claimed to be current ISO 3166-2 suffixes.

## Exact shape

UTF-8 gzipped JSON: `{"metadata": {...}, "regions": [...]}`.

| Row | ID pattern | kind / subtype | parent_id | Additional fields |
|---|---|---|---|---|
| Country | `ZA` | `country` / absent | null | name, official_name |
| Province | `ZA-{province_code}` | `state` / `province` | `ZA` | name, official_name, geoid |
| District | `ZA-COUNTY-{demarcation_code}` | `county` / `district_municipality` | province ID | name, official_name, source_name, geoid, state_id |
| Local | `ZA-PLACE-{demarcation_code}` | `place` / `local_municipality` | province ID | same as district, county_ids: exactly one district ID |
| Metro | `ZA-PLACE-{demarcation_code}` | `place` / `metropolitan_municipality` | province ID | same as district, county_ids: [] |

`official_name == name` for every row. Municipal names append the category to Treasury's short name, e.g.
`Rand West City Local Municipality` and `City of Johannesburg Metropolitan Municipality`.
`source_name` preserves the trimmed source short name. These are explicit municipal display labels, not a separate claim that the legal styled name was verified.
Every local's district is taken directly from `municipality.parent_code`, checked to be active and in the same province.
All 213 terminal rows are municipalities, even when the source short name contains “City”.

Example terminal row:

```json
{"id":"ZA-PLACE-GT485","name":"Rand West City Local Municipality","official_name":"Rand West City Local Municipality","source_name":"Rand West City","geoid":"GT485","kind":"place","subtype":"local_municipality","parent_id":"ZA-GT","state_id":"ZA-GT","county_ids":["ZA-COUNTY-DC48"]}
```

Metadata includes `country`, `catalogue_release`, `as_of`, `downloaded_at`, `vintage`,
`demarcation_changes_vintage`, `demarcation_effective_dates`, `source_fact_count`,
`active_municipality_count`, `excluded_disestablished_codes`, `counts`, and
`sources` (four entries each containing `url` and `sha256`), plus scope/naming/relationship caveats.

## Rebuild and verification

Requires Python standard library and curl. Run with working proxy environment; on this workstation use the home-directory shell:

```bash
cd ~
python3 /Users/zhangnu/outputs/local-lead-workbench/scripts/import_za_geography.py
```

Default snapshot directory is the system temporary directory plus `za-geography-2026`;
four raw JSON responses and `manifest.json` retain provenance for replay.
Use `--source-dir PATH` to retain a durable source snapshot, `--refresh` to explicitly download a new one,
and `--output PATH` to generate elsewhere (including the existing application dataset directory when integrating).
Preserve the snapshot for exact byte reproduction; the live API may change.

The importer rejects incomplete pages, duplicate IDs, unknown categories, inconsistent province names,
missing/cross-province districts, unreviewed code reuse, sources newer than `--as-of`, or changed release counts.
It hash-checks cached sources, validates the decompressed output, and atomically replaces the destination only after success.
Gzip timestamp is fixed to zero; replaying the same snapshot produces identical bytes.

Executed verification: official import; exact 9/44/205/8 counts and 267 IDs; all parent links and 44 district memberships;
35 disestablished codes excluded; all municipality labels explicit; no geometry; byte-identical second build.
Five negative probes rejected truncated responses, duplicate codes, missing districts, inconsistent provinces and unknown categories.
No production business database, credentials, browser profile, regions module or frontend was touched.

Artifact SHA-256: `c0a14b374873b1ea348a20c7ed89f1cc6c2b53ef52a777053b03427ca498d831`.
