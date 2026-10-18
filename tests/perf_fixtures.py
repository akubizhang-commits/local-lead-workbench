"""Synthetic bulk data for performance tests (no network, no real data).

``build`` opens ``Store(dir)`` first so the schema and all migrations exist, then
bulk-inserts with executemany. ``legacy=True`` skips tables that only exist after
the P0 migrations, so the same generator also runs inside a 9423c53 subprocess.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import sys

CATEGORIES = [
    "Roofing supply store", "Metal supplier", "Building materials store", "Sheet metal contractor",
    "Roofing contractor", "Hardware store", "Steel distributor", "Lumber store", "Home improvement store",
    "Construction company", "Wholesaler", "Industrial equipment supplier", "Fastener supplier",
    "Gutter cleaning service", "Siding contractor", "Insulation contractor", "Paint store",
    "Tile store", "Plumbing supply store", "Electrical supply store", "Window supplier",
    "Door supplier", "Concrete contractor", "General contractor", "Welding supply store",
    "Aluminum supplier", "Copper supplier", "Solar energy equipment supplier", "Trailer supply store",
    "Warehouse store",
]
FIRST = ["john", "mary", "carlos", "linda", "ahmed", "susan", "wei", "olga", "peter", "nina"]
LAST = ["smith", "garcia", "nguyen", "brown", "khan", "lopez", "chen", "ivanova", "miller", "rossi"]


def _fp(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def build(directory, companies=5000, contacts_per_company=5, legacy=False, markets=2):
    from leadworkbench.db import Store
    store = Store(directory)
    tables = _tables(sqlite3.connect(store.path))
    stamp = "2026-10-01T00:00:00+00:00"
    with closing(sqlite3.connect(store.path)) as con:
        con.execute("PRAGMA foreign_keys=OFF")
        market_ids = [f"market{m}" for m in range(markets)]
        run_ids = [f"run{m}" for m in range(markets)]
        con.executemany('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)', [
            (mid, f"San Jose {m}", "US", "California", "San Jose", json.dumps([f"kw{m}"]),
             json.dumps(["wholesale"]), 0, 0, stamp) for m, mid in enumerate(market_ids)])
        con.executemany("INSERT INTO market_keys VALUES(?,?)", [(f"key{m}", mid) for m, mid in enumerate(market_ids)])
        con.executemany("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            (rid, market_ids[m], "discovery", None, "completed", "{}", "done", None, stamp, stamp, "{}")
            for m, rid in enumerate(run_ids)])
        con.executemany("INSERT INTO market_runs(run_id,market_id) VALUES(?,?)", list(zip(run_ids, market_ids)))
        rows, listings, locations, observations, memberships, run_companies = [], [], [], [], [], []
        contacts, versions, reviews, evidence, proposals, categories = [], [], [], [], [], []
        pending_contacts = 0
        for i in range(companies):
            cid = f"c{i:06d}"
            created = f"2026-09-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:00+00:00"
            domain = f"co{i}.example"
            rows.append((cid, f"Company {i} Supply", f"{i} Main St, San Jose, CA 95112", f"+1 408-555-{i % 10000:04d}",
                         f"https://{domain}", "San Jose", "US", "[]", "candidate", "", 1, "maps", created, None,
                         "[]", "[]", None))
            key = f"google_maps:place_id:P{i}"
            url = f"https://www.google.com/maps/search/?api=1&query_place_id=P{i}"
            listings.append((key, cid, url, created, created))
            locations.append((f"l{i}", cid, key, f"{i} Main St", 37.3, -121.9, url, created))
            category = CATEGORIES[i % len(CATEGORIES)]
            for field, value in (("name", f"Company {i} Supply"), ("category", category),
                                 ("phone", f"+1 408-555-{i % 10000:04d}")):
                observations.append((f"o{i}{field}", cid, field, value, url, "Google Maps", created, "maps",
                                     run_ids[i % markets], "kw", _fp(cid, field)))
            if "company_categories" in tables and not legacy:
                categories.append((cid, " ".join(category.casefold().split()), category, url, created, created))
            mid = market_ids[i % markets]
            memberships.append((mid, cid, created, created, run_ids[i % markets], run_ids[i % markets]))
            run_companies.append((run_ids[i % markets], cid, mid, "new", 0, created, created))
            first, last = FIRST[i % 10], LAST[(i // 10) % 10]
            people = []
            if i % 10 < 3:
                people.append((f"{first.title()} {last.title()}", "Buyer", f"{first}.{last}@{domain}", "",
                               "procurement", "explicit", "same_block"))
            else:
                people.append((f"{first.title()} {last.title()}", "Sales Manager", f"{first}@{domain}", "",
                               "general", "explicit", "same_block"))
            people += [
                ("Pat Lee", "Operations Manager", f"pat@{domain}", "", "general", "explicit", "same_block"),
                ("", "", f"{first}.{last}@{domain}" if i % 10 >= 3 else f"{last}.{first}@{domain}", "",
                 "general", "unknown", "company_only"),
                ("", "", f"info@{domain}", "", "general", "unknown", "company_only"),
                ("", "", "", f"https://{domain}/contact", "contact_form", "unknown", "company_only"),
            ]
            for j, person in enumerate(people[:contacts_per_company]):
                name, role, email, link, kind, role_status, association = person
                contact_id = f"k{i:06d}{j}"
                page = f"https://{domain}/page{j}"
                evidence_text = f"{name} | {role} | {email or link}"
                contacts.append((contact_id, cid, name, role, email, link, kind, role_status, association,
                                 page, evidence_text, created, "auto_recorded", _fp("contact", contact_id)))
                payload = dict(name=name, role=role, email=email, url=link, kind=kind, role_status=role_status,
                               association_status=association, source_url=page, evidence=evidence_text)
                versions.append((f"v{contact_id}", contact_id, json.dumps(payload), created))
                if j == 0 and pending_contacts < 1000 and i % 5 == 1:
                    pending_contacts += 1
                    rid = f"rc{contact_id}"
                    material = {k: payload[k] for k in ("name", "role", "kind", "role_status",
                                                        "association_status")} | {"evidence": evidence_text}
                    reviews.append((rid, cid, "contact", "contact", json.dumps(material), contact_id, page,
                                    "Other Person | Sales Rep", "pending", created, _fp("review", rid)))
                    evidence.append((rid, _fp("evidence", rid), page, "Other Person | Sales Rep", created))
                    if "contact_change_proposals" in tables and not legacy:
                        proposal = dict(name="Other Person", role="Sales Rep", kind="general", role_status="explicit",
                                        association_status="same_block", evidence="Other Person | Sales Rep",
                                        source_url=page, observed_at=created)
                        proposals.append((rid, contact_id, json.dumps(proposal), _fp("proposal", rid),
                                          "pending", "proposal", created, None))
            if i % 5 == 0:
                for k in range(2):
                    rid = f"rf{i}{k}"
                    reviews.append((rid, cid, "field_update", "phone", f"+1 408-555-{i % 10000:04d}",
                                    f"+1 650-555-{(i + k) % 10000:04d}", f"https://{domain}/contact", "Phone",
                                    "pending", created, _fp("field", rid)))
                    evidence.append((rid, _fp("fe", rid), f"https://{domain}/contact", "Phone", created))
        con.executemany("INSERT INTO companies(id,name,address,phone,website,city,country,business_types,status,notes,"
                        "revision,source_kind,created_at,last_checked_at,overrides,social_links,merged_into) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.executemany("INSERT INTO listings VALUES(?,?,?,?,?)", listings)
        con.executemany("INSERT INTO locations VALUES(?,?,?,?,?,?,?,?)", locations)
        con.executemany("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", observations)
        con.executemany("INSERT INTO market_memberships VALUES(?,?,?,?,?,?)", memberships)
        con.executemany("INSERT INTO run_companies VALUES(?,?,?,?,?,?,?)", run_companies)
        con.executemany("INSERT INTO contacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", contacts)
        con.executemany("INSERT INTO contact_versions VALUES(?,?,?,?)", versions)
        con.executemany("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)", reviews)
        con.executemany("INSERT INTO review_evidence VALUES(?,?,?,?,?)", evidence)
        if proposals:
            con.executemany("INSERT INTO contact_change_proposals VALUES(?,?,?,?,?,?,?,?)", proposals)
        if categories:
            con.executemany("INSERT INTO company_categories VALUES(?,?,?,?,?,?)", categories)
        if "company_type_hints" in tables and not legacy:
            con.executemany("INSERT OR IGNORE INTO company_type_hints VALUES(?,?,?,?)",
                            [(m[1], "wholesaler", m[0], stamp) for m in memberships])
        con.commit()
    return store


def best_of(fn, repeat=3):
    import time
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return round(best, 3)


def legacy_timings(store, repeat=3):
    """Operations available with the same signature on 9423c53 and after P0."""
    from leadworkbench.export import export_data
    return {
        "page_newest": best_of(lambda: store.company_page(page=1, page_size=50), repeat),
        "page_procurement": best_of(lambda: store.company_page(page=1, page_size=50, sort="procurement"), repeat),
        "page_contact_procurement": best_of(lambda: store.company_page(page=1, page_size=50, contact="procurement"), repeat),
        "page_q": best_of(lambda: store.company_page(q="Supply", page=1, page_size=50), repeat),
        "reviews": best_of(store.reviews, repeat),
        "csv": best_of(lambda: export_data(store, "csv"), repeat),
        "xlsx": best_of(lambda: export_data(store, "xlsx"), repeat),
    }


if __name__ == "__main__":
    # python tests/perf_fixtures.py DIR COMPANIES CONTACTS [legacy] [--timings OUT.json]
    args = [a for a in sys.argv[1:] if a != "--timings"]
    out = sys.argv[sys.argv.index("--timings") + 1] if "--timings" in sys.argv else None
    if out:
        args.remove(out)
    built = build(args[0], int(args[1]), int(args[2]), legacy="legacy" in args[3:])
    if out:
        import time
        result = {"companies": int(args[1]), "contacts": int(args[1]) * int(args[2]),
                  "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": legacy_timings(built)}
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)
