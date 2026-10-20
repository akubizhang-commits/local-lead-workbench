"""Review-noise measurement over the real-evidence fixture (design §4.9, §12.3).

    LEAD_DATA_DIR=$(mktemp -d) .venv/bin/python tests/review_noise_measure.py

Writes go through the Store's public paths only: ``refresh``/``apply_enrichment``
(non-market path) and ``create_market``/``start_task``/``ingest_market``/
``add_enrichment``/``apply_enrichment`` (market path), with pages parsed by
``parse_page``. Starting values are seeded with plain SQL, as a database
upgraded from an earlier version would hold them. Every Store lives in its own
temporary directory.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import uuid

from leadworkbench.db import Store
from leadworkbench.sources.website import parse_page

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "review_noise_20261017.json"
GROUPS = ("product", "qa07", "qa09")
# Fields the evidence shows as noise on the market path (design §4.9 1b).
NOISE_FIELDS = {("omni", "name"), ("omni", "phone"), ("superior", "business_types")}
FIELDS = ("name", "phone", "business_types")


def load_fixture():
    return json.loads(FIXTURE.read_text())


@contextmanager
def temp_store():
    with tempfile.TemporaryDirectory() as directory:
        yield Store(directory)


def seed_company(store, company, *, created_at="2026-10-17T08:00:00+00:00"):
    """Insert one starting company row; returns its id."""
    cid = uuid.uuid4().hex
    with store.connect() as con:
        con.execute("""INSERT INTO companies(id,name,address,phone,website,city,country,business_types,
            overrides,source_kind,created_at) VALUES(?,?,?,?,?,?,?,?,?,'maps',?)""", (
            cid, company["name"], company["address"], company["phone"], company["website"], company["city"],
            company["country"], json.dumps(company["business_types"]), json.dumps(company["overrides"]), created_at))
    return cid


def page_payload(company):
    observations = []
    for page in company["pages"]:
        observations.extend(parse_page(page["html"], page["url"])["observations"])
    return {"status": "completed", "observations": observations, "contacts": [], "social_links": []}


def field_reviews(store):
    with store.connect() as con:
        return [dict(r) for r in con.execute("""SELECT company_id,field,old_value,new_value FROM reviews
            WHERE kind='field_update' AND status='pending' ORDER BY company_id,field,new_value""")]


def observations(store, cid):
    with store.connect() as con:
        return [dict(r) for r in con.execute("""SELECT field,value FROM observations
            WHERE company_id=? AND method='website' ORDER BY field,value""", (cid,))]


def measure_generation(fixture=None):
    """Non-market path: a single-company refresh run, then apply_enrichment."""
    fixture = fixture or load_fixture()
    result = {}
    for name in GROUPS:
        with temp_store() as store:
            ids, rows = {}, []
            for company in fixture["groups"][name]["companies"]:
                ids[company["key"]] = seed_company(store, company)
            keys = {cid: key for key, cid in ids.items()}
            for company in fixture["groups"][name]["companies"]:
                if company["pages"]:
                    run = store.refresh(ids[company["key"]])
                    store.apply_enrichment(ids[company["key"]], page_payload(company), run_id=run["id"])
            for review in field_reviews(store):
                rows.append({**review, "company": keys[review.pop("company_id")]})
            result[name] = {"reviews": rows,
                            "observations": {key: observations(store, cid) for key, cid in ids.items()}}
    return result


def measure_market_path(fixture=None):
    """Market path: website pages applied on the market round that found the company on Maps."""
    fixture = fixture or load_fixture()
    result = {}
    for name in GROUPS:
        with temp_store() as store:
            market = store.create_market(dict(country="US", state="California", city="San Jose",
                                              keywords=["metal roofing supply"], company_types=["wholesale"],
                                              auto_enrich=False))
            run = store.start_task(market["id"])
            rewritten, changes, changed, obs = [], [], {}, {}
            for company in fixture["groups"][name]["companies"]:
                if not company["pages"]:
                    continue
                record = {"source_key": f"maps:{company['key']}",
                          "source_url": f"https://www.google.com/maps/place/{company['key']}",
                          "observed_at": "2026-10-17T08:20:00+00:00",
                          **{k: company[k] for k in ("name", "address", "phone", "website", "business_types")}}
                if company["overrides"]:
                    # Maps found the original listing; the user then corrected these fields by hand.
                    record.update(name="Superior Roof Metals", phone="+1 408-539-1935")
                cid = store.ingest_market(record, run["id"], "metal roofing supply", market["id"])["id"]
                if company["overrides"]:
                    current = store.company(cid)
                    store.update_company(cid, current["revision"],
                                         {k: company[k] for k in company["overrides"]})
                with store.connect() as con:
                    before = dict(con.execute(f"SELECT {','.join(FIELDS)} FROM companies WHERE id=?", (cid,)).fetchone())
                    seen = {r[0] for r in con.execute("SELECT id FROM changes WHERE company_id=?", (cid,))}
                store.add_enrichment(run["id"], cid)
                store.apply_enrichment(cid, page_payload(company), run_id=run["id"])
                with store.connect() as con:
                    after = dict(con.execute(f"SELECT {','.join(FIELDS)} FROM companies WHERE id=?", (cid,)).fetchone())
                    changes += [dict(r, company=company["key"]) for r in con.execute(
                        "SELECT id,field,old_value,new_value,reason FROM changes WHERE company_id=? ORDER BY rowid", (cid,))
                        if r["id"] not in seen]
                    changed[company["key"]] = con.execute(
                        "SELECT changed FROM run_companies WHERE run_id=? AND company_id=?", (run["id"], cid)).fetchone()[0]
                rewritten += [{"company": company["key"], "field": f, "before": before[f], "after": after[f]}
                              for f in FIELDS if before[f] != after[f]]
                obs[company["key"]] = observations(store, cid)
            reviews = field_reviews(store)
            result[name] = {"rewritten": rewritten,
                            "noise_rewritten": [r for r in rewritten if (r["company"], r["field"]) in NOISE_FIELDS],
                            "changes": changes, "changed": changed, "reviews": len(reviews), "observations": obs}
    return result


def seed_baseline(store, group, fixture=None):
    """Starting companies plus the evidence's original review rows inserted as pending
    (an upgraded database that still holds the noise). Returns {key: company_id}."""
    fixture = fixture or load_fixture()
    ids = {company["key"]: seed_company(store, company) for company in fixture["groups"][group]["companies"]}
    with store.connect() as con:
        for n, review in enumerate(fixture["groups"][group]["baseline"]):
            old, new = review["old_value"], review["new_value"]
            if review["kind"] == "duplicate_candidate":
                old, new = ids[old], ids[new]
            con.execute("""INSERT INTO reviews(id,company_id,kind,field,old_value,new_value,source_url,evidence,
                status,created_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,'pending',?,?)""", (
                uuid.uuid4().hex, ids[review["company"]], review["kind"], review["field"], old, new,
                review["source_url"], review["evidence"], f"2026-10-17T08:{n:02d}:00+00:00", uuid.uuid4().hex))
    return ids


def pending_count(store):
    with store.connect() as con:
        return con.execute("SELECT count(*) FROM reviews WHERE status='pending'").fetchone()[0]


def measure_cleanup(fixture=None):
    """Per group: pending before, closed by the one-time cleanup, pending after."""
    fixture = fixture or load_fixture()
    result = {}
    for name in GROUPS:
        with temp_store() as store:
            seed_baseline(store, name, fixture)
            before = pending_count(store)
            preview = store.review_cleanup_preview()
            closed = store.review_cleanup(preview["plan_id"])
            result[name] = {"before": before, "closed": closed["closed"], "after": pending_count(store),
                            "rules": closed["rules"]}
    return result


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def report(fixture=None):
    fixture = fixture or load_fixture()
    generation, market = measure_generation(fixture), measure_market_path(fixture)
    out = ["# 降噪度量（tests/review_noise_measure.py）", "",
           "## 非市场路径：重新采集后生成的字段审核", ""]
    rows, total_base, total_now = [], 0, 0
    for name in GROUPS:
        base = sum(r["kind"] == "field_update" for r in fixture["groups"][name]["baseline"])
        now = len(generation[name]["reviews"])
        total_base, total_now = total_base + base, total_now + now
        detail = "；".join(f"{r['company']}.{r['field']}: {r['old_value']} → {r['new_value']}"
                          for r in generation[name]["reviews"]) or "—"
        rows.append((name, base, now, detail))
    rows.append(("合计", total_base, total_now, ""))
    out += [_table(("组", "证据中的字段审核", "本次实测字段审核", "实测审核明细"), rows), "",
            "## 市场路径：官网补全挂在市场轮次上时被改写的企业字段", ""]
    rows = []
    for name in GROUPS:
        m = market[name]
        rows.append((name, len(m["noise_rewritten"]), len(m["rewritten"]),
                     "；".join(f"{r['company']}.{r['field']}: {r['before']} → {r['after']}" for r in m["rewritten"]) or "—",
                     ", ".join(f"{k}={v}" for k, v in m["changed"].items()), m["reviews"]))
    out.append(_table(("组", "噪音字段被改写", "全部被改写字段", "改写明细", "run_companies.changed", "字段审核"), rows))
    cleanup, rows, totals = measure_cleanup(fixture), [], [0, 0, 0]
    for name in GROUPS:
        c = cleanup[name]
        totals = [totals[0] + c["before"], totals[1] + c["closed"], totals[2] + c["after"]]
        rows.append((name, c["before"], c["closed"], c["after"],
                     ", ".join(f"{k}={v}" for k, v in sorted(c["rules"].items())) or "—"))
    rows.append(("合计", *totals, ""))
    out += ["", "## 一次性审核清理：证据中的原始审核按待审核插入后", "",
            _table(("组", "清理前待审核", "清理关闭", "清理后待审核", "规则"), rows)]
    return "\n".join(out) + "\n"


def main():
    sys.stdout.write(report())


if __name__ == "__main__":
    main()
