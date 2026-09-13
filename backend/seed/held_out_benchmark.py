"""Held-out mapping benchmark — hand-labeled real-world column names.

`generate_seed_data.py`'s benchmark has a leakage problem: its legacy
column names (CustID, COMPNAME, CNTRY, IND_CD, DT_CREATE, VATNO, EMPCT,
...) were written at the same time as `app/canonical.py`'s alias lists,
and the aliases quietly include the exact same abbreviations
(custid, compname, cntry, ind_cd, dt_create, vatno, empct). Fuzzy
name-matching "solving" that benchmark proves nothing — the answer key
and the test set were written by the same hand. See docs/PROGRESS.md.

Every column name here comes from a real SaaS export (Salesforce,
HubSpot, QuickBooks, Zendesk) and was chosen without looking at
app/canonical.py's alias lists. This is the benchmark that actually
tests generalization to a source system the scorer has never seen.

Each entry: (source_kind, column_name, sample_values, true_field).
true_field is None where the column genuinely has no canonical
equivalent — correctly abstaining on these matters as much as mapping
the real ones.
"""

HeldOutColumn = tuple[str, str, list[str], str | None]

HELD_OUT_COLUMNS: list[HeldOutColumn] = [
    # --- Salesforce (crm) ---
    ("crm", "AccountId", ["001xx000003DGT2AAO", "001xx000003DGT3AAO", "001xx000003DGT4AAO"], "customer_natural_key"),
    ("crm", "Name", ["Acme Corp", "Globex Inc", "Initech LLC"], "legal_name"),
    ("crm", "BillingCountry", ["United States", "Canada", "Germany"], "country"),
    ("crm", "Phone", ["555-0100", "555-0101", "555-0102"], "customer_phone"),
    ("crm", "Email", ["a@acme.com", "b@globex.com", "c@initech.com"], "customer_email"),
    ("crm", "Sic", ["7372", "2834", "3674"], "industry"),
    ("crm", "NumberOfEmployees", ["120", "4500", "80"], "customer_employee_count"),
    ("crm", "CreatedDate", ["2023-05-01", "2022-11-15", "2024-01-20"], "customer_created_at"),
    ("crm", "AccountSource", ["Web", "Referral", "Partner"], None),
    # --- HubSpot (crm) ---
    ("crm", "hs_object_id", ["5012345", "5012346", "5012347"], "customer_natural_key"),
    ("crm", "createdate", ["2023-05-01T10:00:00Z", "2022-11-15T08:30:00Z", "2024-01-20T12:00:00Z"], "customer_created_at"),
    ("crm", "company", ["Acme Corp", "Globex Inc", "Initech LLC"], "legal_name"),
    ("crm", "numemployees", ["120", "4500", "80"], "customer_employee_count"),
    ("crm", "lifecyclestage", ["lead", "customer", "opportunity"], None),
    # --- QuickBooks (billing) ---
    ("billing", "TxnDate", ["2026-01-15", "2026-02-20", "2026-03-01"], "invoice_issue_date"),
    ("billing", "DocNumber", ["1001", "1002", "1003"], "invoice_natural_key"),
    ("billing", "CustomerRef", ["C-1001", "C-1002", "C-1003"], "invoice_customer_natural_key"),
    ("billing", "DueDate", ["2026-02-15", "2026-03-20", "2026-04-01"], "invoice_due_date"),
    ("billing", "TotalAmt", ["1500.00", "2300.50", "999.99"], "invoice_amount_minor_units"),
    ("billing", "CurrencyRef", ["USD", "EUR", "GBP"], "invoice_currency"),
    ("billing", "Balance", ["500.00", "0.00", "1200.00"], None),
    ("billing", "PrivateNote", ["Called customer", "", "Left voicemail"], None),
    # --- Zendesk (support) ---
    ("support", "requester_id", ["9001", "9002", "9003"], "ticket_customer_natural_key"),
    ("support", "priority", ["low", "high", "urgent"], "ticket_priority"),
    ("support", "status", ["open", "pending", "solved"], "ticket_status"),
    ("support", "created_at", ["2026-01-10T09:00:00Z", "2026-02-11T14:30:00Z", "2026-03-01T08:00:00Z"], "ticket_opened_at"),
    ("support", "subject", ["Cannot log in", "Refund request", "API access"], "ticket_subject"),
    ("support", "via_channel", ["email", "web", "chat"], None),
    ("support", "satisfaction_rating", ["good", "bad", "offered"], None),
    ("support", "id", ["TCK-9001", "TCK-9002", "TCK-9003"], "ticket_natural_key"),
]
