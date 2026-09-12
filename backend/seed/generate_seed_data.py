"""Deterministic seed-data generator for ConduitAI.

Produces four deliberately messy CSV exports (crm_snake, crm_legacy,
billing, support) plus ground_truth.json documenting (a) the true
source-column -> canonical-field mapping for every column across all
four files (the Day 3 mapping-accuracy benchmark), and (b) the row-level
defects deliberately injected (the Day 4/5 validation-coverage and
correction-rate benchmark).

Deterministic: random.seed(SEED) and Faker.seed(SEED) are set before every
call to generate(), so re-running produces byte-identical output and
accuracy/coverage numbers are comparable run over run.
"""

import csv
import json
import random
from pathlib import Path

from faker import Faker

from app.canonical import CANONICAL_FIELDS

SEED = 42
NUM_CUSTOMERS = 30
OUTPUT_DIR = Path(__file__).parent / "output"

COUNTRIES = ["United States", "United Kingdom", "Germany", "India", "Canada"]
INDUSTRIES = ["Finance", "Healthcare", "Retail", "Manufacturing", "Technology"]
RISK_TIERS = ["low", "medium", "high"]
REGIONS = ["NA", "EMEA", "APAC"]
INVOICE_STATUSES = ["open", "paid", "void", "overdue"]
TICKET_PRIORITIES = ["low", "medium", "high", "urgent"]
TICKET_STATUSES = ["open", "pending", "closed"]

# source_column -> canonical field name, or None if deliberately unmapped
CRM_COLUMN_MAP = {
    "customer_id": "customer_natural_key",
    "company_name": "legal_name",
    "display_name": "display_name",
    "email": "customer_email",
    "phone": "customer_phone",
    "country": "country",
    "industry": "industry",
    "risk_tier": "risk_tier",
    "created_at": "customer_created_at",
    "notes": None,
    "vat_number": "customer_vat_number",
    "employee_count": "customer_employee_count",
    "region": "customer_region",
}
CRM_LEGACY_HEADER_RENAME = {
    "customer_id": "CustID",
    "company_name": "COMPNAME",
    "display_name": "DISPNAME",
    "email": "EMAIL",
    "phone": "PHONE",
    "country": "CNTRY",
    "industry": "IND_CD",
    "risk_tier": "RISK",
    "created_at": "DT_CREATE",
    "notes": "NOTES",
    "vat_number": "VATNO",
    "employee_count": "EMPCT",
    "region": "REGION",
}
BILLING_COLUMN_MAP = {
    "customer_id": "invoice_customer_natural_key",
    "invoice_number": "invoice_natural_key",
    "amount": "invoice_amount_minor_units",
    "currency": "invoice_currency",
    "issue_date": "invoice_issue_date",
    "due_date": "invoice_due_date",
    "status": "invoice_status",
    "account_number": "account_number",
    "payment_method": None,
    "memo": None,
    "tax_amount": "invoice_tax_amount_minor_units",
    "po_number": "invoice_po_number",
}
SUPPORT_COLUMN_MAP = {
    "customer_id": "ticket_customer_natural_key",
    "ticket_ref": "ticket_natural_key",
    "subject": "ticket_subject",
    "priority": "ticket_priority",
    "status": "ticket_status",
    "opened_at": "ticket_opened_at",
    "closed_at": "ticket_closed_at",
    "channel": None,
    "assignee": None,
    "account_number": "account_number",
}


def _validate_column_map(column_map: dict) -> None:
    valid_names = {f.name for f in CANONICAL_FIELDS}
    for source_col, canonical_name in column_map.items():
        if canonical_name is not None and canonical_name not in valid_names:
            raise ValueError(
                f"Column '{source_col}' maps to unknown canonical field '{canonical_name}'"
            )


def _build_customers(fake: Faker) -> list[dict]:
    customers = []
    for i in range(NUM_CUSTOMERS):
        internal_id = 1001 + i
        customers.append(
            {
                "crm_id": f"C-{internal_id}",
                "billing_id": str(internal_id),
                "company_name": fake.company(),
                "display_name": fake.company_suffix(),
                "email": fake.company_email(),
                "phone": fake.phone_number(),
                "country": random.choice(COUNTRIES),
                "industry": random.choice(INDUSTRIES),
                "risk_tier": random.choice(RISK_TIERS),
                "created_at": fake.date_between(start_date="-3y", end_date="-1y"),
                "account_number": f"ACC-{internal_id}",
                "vat_number": f"VAT{random.randint(100000, 999999)}",
                "employee_count": random.randint(5, 5000),
                "region": random.choice(REGIONS),
            }
        )
    return customers


def _write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_crm_rows(customers: list[dict]) -> tuple[list[dict], dict]:
    rows = [
        {
            "customer_id": c["crm_id"],
            "company_name": c["company_name"],
            "display_name": c["display_name"],
            "email": c["email"],
            "phone": c["phone"],
            "country": c["country"],
            "industry": c["industry"],
            "risk_tier": c["risk_tier"],
            "created_at": c["created_at"].isoformat(),
            "notes": "",
            "vat_number": c["vat_number"],
            "employee_count": str(c["employee_count"]),
            "region": c["region"],
        }
        for c in customers
    ]
    defects: dict[str, list[str]] = {}

    rows[2]["email"] = ""
    defects["2"] = ["MISSING_REQUIRED"]

    dup = dict(rows[0])
    dup["customer_id"] = "C-9001"
    dup["company_name"] = rows[0]["company_name"] + " Ltd"
    rows.append(dup)
    defects[str(len(rows) - 1)] = ["DUPLICATE_FUZZY"]

    rows[5]["created_at"] = "03/04/2025"
    defects["5"] = ["DATE_AMBIGUOUS"]

    rows[7]["company_name"] = "Café Système " + rows[7]["company_name"]

    return rows, defects


def _build_billing_rows(customers: list[dict], fake: Faker) -> tuple[list[dict], dict]:
    rows = [
        {
            "customer_id": c["billing_id"],
            "invoice_number": f"INV-{2000 + idx}",
            "amount": f"{random.uniform(100, 5000):.2f}",
            "currency": "USD",
            "issue_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "due_date": fake.date_between(start_date="today", end_date="+30d").isoformat(),
            "status": random.choice(INVOICE_STATUSES),
            "account_number": c["account_number"],
            "payment_method": random.choice(["wire", "card", "ach"]),
            "memo": "",
            "tax_amount": f"{random.uniform(5, 400):.2f}",
            "po_number": f"PO-{4000 + idx}",
        }
        for idx, c in enumerate(customers)
    ]
    defects: dict[str, list[str]] = {}

    rows[3]["amount"] = "-450.00"
    defects["3"] = ["BUSINESS_RULE_NEGATIVE_AMOUNT"]

    rows[6]["issue_date"] = "2099-01-01"
    defects["6"] = ["BUSINESS_RULE_FUTURE_DATE"]

    rows[8]["issue_date"] = "03/04/2025"
    defects["8"] = ["DATE_AMBIGUOUS"]

    rows[10]["amount"] = "$1,234.56"

    rows.append(
        {
            "customer_id": "9999",
            "invoice_number": "INV-9999",
            "amount": "775.00",
            "currency": "USD",
            "issue_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "due_date": fake.date_between(start_date="today", end_date="+30d").isoformat(),
            "status": "open",
            "account_number": "ACC-9999",
            "payment_method": "wire",
            "memo": "",
            "tax_amount": "62.00",
            "po_number": "PO-9999",
        }
    )
    defects[str(len(rows) - 1)] = ["REF_INTEGRITY_ORPHAN_FK"]

    return rows, defects


def _build_support_rows(customers: list[dict], fake: Faker) -> tuple[list[dict], dict]:
    rows = [
        {
            "customer_id": c["billing_id"],
            "ticket_ref": f"TCK-{3000 + idx}",
            "subject": fake.sentence(nb_words=6),
            "priority": random.choice(TICKET_PRIORITIES),
            "status": random.choice(TICKET_STATUSES),
            "opened_at": fake.date_between(start_date="-1y", end_date="today").isoformat(),
            "closed_at": "",
            "channel": random.choice(["email", "phone", "chat"]),
            "assignee": fake.first_name(),
            "account_number": c["account_number"],
        }
        for idx, c in enumerate(customers)
    ]
    defects: dict[str, list[str]] = {}

    rows[4]["customer_id"] = ""
    defects["4"] = ["MISSING_REQUIRED"]

    rows.append(dict(rows[1]))
    defects[str(len(rows) - 1)] = ["DUPLICATE_EXACT"]

    return rows, defects


def generate() -> None:
    for column_map in (CRM_COLUMN_MAP, BILLING_COLUMN_MAP, SUPPORT_COLUMN_MAP):
        _validate_column_map(column_map)

    random.seed(SEED)
    Faker.seed(SEED)
    fake = Faker()

    customers = _build_customers(fake)

    crm_rows, crm_defects = _build_crm_rows(customers)
    crm_headers = list(CRM_COLUMN_MAP.keys())
    _write_csv(OUTPUT_DIR / "crm_snake.csv", crm_headers, crm_rows)

    legacy_headers = [CRM_LEGACY_HEADER_RENAME[h] for h in crm_headers]
    legacy_rows = [
        {CRM_LEGACY_HEADER_RENAME[k]: v for k, v in row.items()} for row in crm_rows
    ]
    _write_csv(OUTPUT_DIR / "crm_legacy.csv", legacy_headers, legacy_rows)

    billing_rows, billing_defects = _build_billing_rows(customers, fake)
    _write_csv(OUTPUT_DIR / "billing.csv", list(BILLING_COLUMN_MAP.keys()), billing_rows)

    support_rows, support_defects = _build_support_rows(customers, fake)
    _write_csv(OUTPUT_DIR / "support.csv", list(SUPPORT_COLUMN_MAP.keys()), support_rows)

    ground_truth = {
        "column_mappings": {
            "crm_snake.csv": CRM_COLUMN_MAP,
            "crm_legacy.csv": {
                CRM_LEGACY_HEADER_RENAME[k]: v for k, v in CRM_COLUMN_MAP.items()
            },
            "billing.csv": BILLING_COLUMN_MAP,
            "support.csv": SUPPORT_COLUMN_MAP,
        },
        "expected_defects": {
            "crm_snake.csv": crm_defects,
            "billing.csv": billing_defects,
            "support.csv": support_defects,
        },
    }
    (OUTPUT_DIR / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2))

    total_columns = sum(len(m) for m in ground_truth["column_mappings"].values())
    mapped_columns = sum(
        1
        for mapping in ground_truth["column_mappings"].values()
        for canonical_name in mapping.values()
        if canonical_name is not None
    )
    print(
        f"Wrote {len(crm_rows)} CRM rows, {len(billing_rows)} billing rows, "
        f"{len(support_rows)} support rows to {OUTPUT_DIR}"
    )
    print(
        f"Labeled benchmark size: {total_columns} columns "
        f"({mapped_columns} mapped) across "
        f"{len(ground_truth['column_mappings'])} files"
    )


if __name__ == "__main__":
    generate()
