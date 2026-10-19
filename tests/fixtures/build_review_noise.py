"""Build tests/fixtures/review_noise_20261017.json from the 2026-10-17 product-review evidence.

Run once by hand (the generated JSON is committed; tests never read .agents):

    .venv/bin/python tests/fixtures/build_review_noise.py [evidence_dir]

evidence_dir defaults to the main repository's
.agents/tasks/product-review-2026-10-17. Three groups (product, qa07, qa09) keep
the company starting values, minimal rebuilt HTML pages and the original pending
review rows (the noise baseline, design §1 / §4.9). Phones are public business
numbers; no contact emails are copied.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = Path("/Users/zhangnu/outputs/local-lead-workbench/.agents/tasks/product-review-2026-10-17")
OUT = HERE / "review_noise_20261017.json"

OMNI_TITLE = "Omni Sheet Metal – Roofing, Panel Systems, Ornamental, Sun Control, Silicon Valley"


def omni_home():
    # Original <title>, no og:site_name; the published tel: link with its leading space.
    return ("<html><head><title>" + OMNI_TITLE + "</title></head><body>"
            '<main><p>Call us <a href="tel: (408) 435-5020">(408) 435-5020</a></p></main></body></html>')


def superior_home():
    # og:site_name as observed; the home page text that filled business_types=[] -> ["retailer"]
    # in the first scan (qa/live/09 changes) is rebuilt as one short retail sentence.
    return ('<html><head><title>Superior Roof Metals</title>'
            '<meta property="og:site_name" content="Superior Roof Metals"></head><body>'
            "<main><p>Roofing and gutter supplies with a retail counter open to contractors and homeowners.</p>"
            '<p><a href="tel:4082950911">(408) 295-0911</a></p></main></body></html>')


def superior_careers(jd):
    return ("<html><head><title>Careers</title></head><body><main><h1>Careers</h1>"
            f"<p>{jd}</p></main></body></html>")


def review_row(row, key):
    return {
        "company": key, "kind": row["kind"], "field": row["field"],
        "old_value": row["old_value"], "new_value": row["new_value"],
        "source_url": row["source_url"], "evidence": row["evidence"],
    }


def qxo_pair(review):
    left, right = review["comparison"]["left"], review["comparison"]["right"]
    keys = {left["id"]: "qxo_left", review["company_id"]: "qxo_right"}
    companies = [
        {"key": keys[side["id"]], "name": side["name"], "phone": side["phone"], "website": side["website"],
         "address": side["address"], "city": side["city"], "country": side["country"],
         "business_types": [], "overrides": [], "pages": []}
        for side in (left, right)]
    row = review_row(review, keys[review["company_id"]])
    row["old_value"], row["new_value"] = keys[review["old_value"]], keys[review["new_value"]]
    return companies, row


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT
    product = json.loads((root / "product/05-reviews.json").read_text())
    qa07 = json.loads((root / "qa/live/07-reviews-after-scan1.json").read_text())
    qa09 = json.loads((root / "qa/live/09-scan2-manual-protect.json").read_text())
    jd = next(r["evidence"] for r in product if r["field"] == "business_types")
    omni = {"key": "omni", "name": "Omni Sheet Metal, Inc.", "phone": "+1 408-435-5020",
            "website": "https://omnisheetmetal.com/", "address": "1010 Olinder Ct, San Jose, CA 95122, United States",
            "city": "San Jose", "country": "US", "business_types": [], "overrides": [],
            "pages": [{"url": "https://omnisheetmetal.com/", "html": omni_home()}]}
    superior_pages = [{"url": "https://superiorroofmetals.com/", "html": superior_home()},
                      {"url": "https://superiorroofmetals.com/careers/", "html": superior_careers(jd)}]
    superior = {"key": "superior", "name": "Superior Roof Metals", "phone": "+1 408-539-1935",
                "website": "https://superiorroofmetals.com/", "address": "890 Faulstich Ct, San Jose, CA 95112, United States",
                "city": "San Jose", "country": "US", "business_types": ["retailer"], "overrides": [],
                "pages": superior_pages}
    company_keys = {"Omni Sheet Metal, Inc.": "omni", "Superior Roof Metals": "superior"}

    def group(reviews, companies):
        rows = []
        for review in reviews:
            if review["kind"] == "duplicate_candidate":
                pair, row = qxo_pair(review)
                companies.extend(pair)
                rows.append(row)
            else:
                rows.append(review_row(review, company_keys[review["company_name"]]))
        return {"companies": companies, "baseline": rows}

    detail = qa09["detail"]["company"]
    manual = {**superior, "name": detail["name"], "phone": detail["phone"],
              "business_types": detail["business_types"], "overrides": detail["overrides"]}
    groups = {
        "product": group(product, [dict(omni), dict(superior)]),
        "qa07": group(qa07, [dict(omni)]),
        "qa09": {"companies": [manual], "baseline": [review_row(r, "superior") for r in qa09["reviews"]]},
    }
    fixture = {
        "source": "product-review-2026-10-17: product/05-reviews.json, qa/live/07-reviews-after-scan1.json, "
                  "qa/live/09-scan2-manual-protect.json",
        "groups": groups,
    }
    OUT.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n")
    print(OUT, {name: len(g["baseline"]) for name, g in groups.items()})


if __name__ == "__main__":
    main(sys.argv)
