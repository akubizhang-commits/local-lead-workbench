"""Safe business exports. Refusal records are excluded."""
import csv
import io
from openpyxl import Workbook
from .contact_projection import group_contact_records


def safe(value):
    if value is None:
        return ""
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def export_data(store, format="csv", **filters):
    if filters.get("run_id") and not filters.get("market_id"):
        market_id = store.run(filters["run_id"]).get("task_id")
        if market_id:
            filters["market_id"] = market_id
    companies = [c for c in store.companies(**filters, max_rows=None) if c["status"] != "do_not_contact"]
    with store.connect() as con:
        for company in companies:
            source = con.execute("""SELECT source_url,observed_at FROM observations WHERE company_id=?
                ORDER BY observed_at DESC LIMIT 1""", (company["id"],)).fetchone()
            company.update(dict(source) if source else {"source_url": "", "observed_at": ""})
    headers = ["id", "name", "address", "phone", "website", "city", "country", "business_types", "status",
               "source_kind", "notes", "created_at", "last_checked_at", "source_url", "observed_at"]
    if filters.get("market_id"):
        headers += ["market_segment", "market_changed", "first_seen", "last_seen"]
    rows = [[safe(c.get(k)) for k in headers] for c in companies]
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)
        return ("\ufeff" + stream.getvalue()).encode("utf-8")
    book = Workbook()
    sheet = book.active
    sheet.title = "Companies"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    contact_keys = ["company_id", "name", "role", "email", "url", "kind", "role_status",
                    "association_status", "review_status", "source_url", "evidence", "observed_at"]
    contacts = book.create_sheet("Contacts")
    contacts.append(contact_keys)
    locations = book.create_sheet("Locations")
    loc_keys = ["company_id", "address", "latitude", "longitude", "source_url", "observed_at"]
    locations.append(loc_keys)
    evidence = book.create_sheet("Sources")
    obs_keys = ["company_id", "field", "value", "source_url", "evidence", "observed_at", "method", "query"]
    evidence.append(obs_keys)
    with store.connect() as con:
        for company in companies:
            records = [row for row in store.contact_records(con, company["id"])
                       if row.get("review_status") not in {"pending", "reject", "invalid", "superseded"}
                       and row.get("adoption_status") != "excluded"]
            from .contact_projection import categorize_outreach
            for row in categorize_outreach(group_contact_records(records, company.get("website", ""))):
                if row.get('outreach_category') == 'irrelevant':
                    continue
                contacts.append([safe(row.get(k)) for k in contact_keys])
                for source in row["sources"]:
                    evidence.append([safe({'company_id':company['id'], 'field':'contact',
                        'value':row.get('email') or row.get('name') or row.get('url'),
                        'method':'website', **source}.get(k)) for k in obs_keys])
            for row in con.execute("SELECT * FROM locations WHERE company_id=?", (company["id"],)):
                locations.append([safe(row[k]) for k in loc_keys])
            for row in con.execute("SELECT * FROM observations WHERE company_id=?", (company["id"],)):
                evidence.append([safe(row[k]) for k in obs_keys])
    from openpyxl.styles import Font, PatternFill
    for ws in book:
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="173C46")
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = min(55, max(14, max(len(str(c.value or "")) for c in column[:40]) + 2))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
