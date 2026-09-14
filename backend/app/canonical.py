"""Canonical field registry.

Single source of truth for what a "canonical field" is: which table/column
it writes to, its type, whether it's required, a human-readable description
(the embedding text for Day 2/3 AI-assisted column mapping), and known
aliases (used by the deterministic fuzzy/alias matcher before any
embedding or LLM call happens). This module describes fields only — it
does not score, map, or transform anything.
"""

from enum import Enum

from pydantic import BaseModel


class FieldType(str, Enum):
    STRING = "string"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    INTEGER = "integer"
    MONEY_MINOR_UNITS = "money_minor_units"
    CURRENCY_CODE = "currency_code"


class CanonicalField(BaseModel):
    name: str
    target_table: str
    target_column: str
    type: FieldType
    required: bool
    description: str
    aliases: list[str] = []
    enum_values: list[str] | None = None


CANONICAL_FIELDS: list[CanonicalField] = [
    # --- customers ---
    CanonicalField(
        name="customer_natural_key",
        target_table="customers",
        target_column="natural_key",
        type=FieldType.STRING,
        required=True,
        description="The source system's unique identifier for this customer, used to detect the same customer across re-uploads and across systems.",
        aliases=["customer_id", "cust_id", "custid", "client_ref", "client_id"],
    ),
    CanonicalField(
        name="legal_name",
        target_table="customers",
        target_column="legal_name",
        type=FieldType.STRING,
        required=True,
        description="The customer's full legal or registered company name.",
        aliases=["company_name", "compname", "legal_name", "account_name"],
    ),
    CanonicalField(
        name="display_name",
        target_table="customers",
        target_column="display_name",
        type=FieldType.STRING,
        required=False,
        description="A shorter, human-friendly name for the customer used in UI display.",
        aliases=["display_name", "short_name", "dispname", "nickname"],
    ),
    CanonicalField(
        name="customer_email",
        target_table="customers",
        target_column="email",
        type=FieldType.EMAIL,
        required=False,
        description="Primary contact email address for the customer.",
        aliases=["email", "e-mail", "contact_email", "primary_email", "email_address"],
    ),
    CanonicalField(
        name="customer_phone",
        target_table="customers",
        target_column="phone",
        type=FieldType.PHONE,
        required=False,
        description="Primary contact phone number for the customer.",
        aliases=["phone", "phone_number", "contact_phone", "tel"],
    ),
    CanonicalField(
        name="country",
        target_table="customers",
        target_column="country",
        type=FieldType.STRING,
        required=False,
        description="The country the customer is based in, as a name or ISO code.",
        aliases=["country", "cntry", "country_code", "nation"],
    ),
    CanonicalField(
        name="industry",
        target_table="customers",
        target_column="industry",
        type=FieldType.STRING,
        required=False,
        description="The industry or business sector the customer operates in.",
        aliases=["industry", "ind_cd", "sector", "vertical"],
    ),
    CanonicalField(
        name="risk_tier",
        target_table="customers",
        target_column="risk_tier",
        type=FieldType.ENUM,
        required=False,
        description="The customer's assigned risk classification tier.",
        aliases=["risk_tier", "risk", "risk_level", "risk_class"],
        enum_values=["low", "medium", "high"],
    ),
    # Added Day 6 -- the schema-evolution demo's new required field. Not
    # backdated onto risk_tier above (which already existed, optional,
    # since Day 1) so the "customer adds a genuinely new required field"
    # narrative stays honest.
    CanonicalField(
        name="kyc_status",
        target_table="customers",
        target_column="kyc_status",
        type=FieldType.ENUM,
        required=True,
        description="Know-Your-Customer verification status for this customer record.",
        aliases=["kyc_status", "kyc", "kyc_state", "verification_status"],
        enum_values=["pending", "verified", "rejected"],
    ),
    CanonicalField(
        name="customer_created_at",
        target_table="customers",
        target_column="created_at",
        type=FieldType.DATE,
        required=False,
        description="The date this customer record was first created in the source system.",
        aliases=["created_at", "dt_create", "date_created", "created_date", "signup_date"],
    ),
    CanonicalField(
        name="customer_vat_number",
        target_table="customers",
        target_column="vat_number",
        type=FieldType.STRING,
        required=False,
        description="The customer's VAT or tax registration number.",
        aliases=["vat_number", "vat_no", "vatno", "tax_id"],
    ),
    CanonicalField(
        name="customer_employee_count",
        target_table="customers",
        target_column="employee_count",
        type=FieldType.INTEGER,
        required=False,
        description="The approximate number of employees at the customer organization.",
        aliases=["employee_count", "num_employees", "headcount", "empct"],
    ),
    CanonicalField(
        name="customer_region",
        target_table="customers",
        target_column="region",
        type=FieldType.STRING,
        required=False,
        description="The sales or geographic region the customer is assigned to.",
        aliases=["region", "sales_region", "territory"],
    ),
    # --- accounts ---
    CanonicalField(
        name="account_customer_natural_key",
        target_table="accounts",
        target_column="customer_id",
        type=FieldType.STRING,
        required=True,
        description="The natural key of the customer this account belongs to; resolved to customers.id during transform, not written directly.",
        aliases=["customer_id", "cust_id", "client_id"],
    ),
    CanonicalField(
        name="account_number",
        target_table="accounts",
        target_column="account_number",
        type=FieldType.STRING,
        required=True,
        description="The unique account number or identifier assigned to this account.",
        aliases=["account_number", "acct_no", "account_id"],
    ),
    CanonicalField(
        name="account_status",
        target_table="accounts",
        target_column="status",
        type=FieldType.ENUM,
        required=False,
        description="The current status of the account.",
        aliases=["status", "account_status", "acct_status"],
        enum_values=["active", "closed", "suspended"],
    ),
    CanonicalField(
        name="account_opened_at",
        target_table="accounts",
        target_column="opened_at",
        type=FieldType.DATE,
        required=False,
        description="The date the account was opened.",
        aliases=["opened_at", "open_date", "start_date"],
    ),
    # --- invoices ---
    CanonicalField(
        name="invoice_natural_key",
        target_table="invoices",
        target_column="natural_key",
        type=FieldType.STRING,
        required=True,
        description="The source system's unique identifier for this invoice.",
        aliases=["invoice_number", "invoice_id", "inv_no", "invoice_ref"],
    ),
    CanonicalField(
        name="invoice_customer_natural_key",
        target_table="invoices",
        target_column="customer_id",
        type=FieldType.STRING,
        required=True,
        description="The natural key of the customer this invoice belongs to; resolved to customers.id during transform, not written directly.",
        aliases=["customer_id", "cust_id", "client_id"],
    ),
    CanonicalField(
        name="invoice_amount_minor_units",
        target_table="invoices",
        target_column="amount_minor_units",
        type=FieldType.MONEY_MINOR_UNITS,
        required=True,
        description="The invoice amount, stored as an integer count of the currency's minor units (e.g. cents) to avoid floating point error.",
        aliases=["amount", "amt_usd", "amount_usd", "total", "invoice_amount"],
    ),
    CanonicalField(
        name="invoice_currency",
        target_table="invoices",
        target_column="currency",
        type=FieldType.CURRENCY_CODE,
        required=True,
        description="The ISO 4217 currency code the invoice amount is denominated in.",
        aliases=["currency", "curr", "ccy"],
    ),
    CanonicalField(
        name="invoice_issue_date",
        target_table="invoices",
        target_column="issue_date",
        type=FieldType.DATE,
        required=True,
        description="The date the invoice was issued.",
        aliases=["issue_date", "invoice_date", "date_issued"],
    ),
    CanonicalField(
        name="invoice_due_date",
        target_table="invoices",
        target_column="due_date",
        type=FieldType.DATE,
        required=False,
        description="The date payment for the invoice is due.",
        aliases=["due_date", "payment_due", "dt_due"],
    ),
    CanonicalField(
        name="invoice_status",
        target_table="invoices",
        target_column="status",
        type=FieldType.ENUM,
        required=False,
        description="The current payment status of the invoice.",
        aliases=["status", "invoice_status", "payment_status"],
        enum_values=["open", "paid", "void", "overdue"],
    ),
    CanonicalField(
        name="invoice_tax_amount_minor_units",
        target_table="invoices",
        target_column="tax_amount_minor_units",
        type=FieldType.MONEY_MINOR_UNITS,
        required=False,
        description="The tax amount charged on this invoice, stored as an integer count of the currency's minor units.",
        aliases=["tax_amount", "tax", "vat_amount"],
    ),
    CanonicalField(
        name="invoice_po_number",
        target_table="invoices",
        target_column="po_number",
        type=FieldType.STRING,
        required=False,
        description="The purchase order number the customer referenced for this invoice.",
        aliases=["po_number", "purchase_order", "po_ref"],
    ),
    # --- support tickets ---
    CanonicalField(
        name="ticket_natural_key",
        target_table="support_tickets",
        target_column="natural_key",
        type=FieldType.STRING,
        required=True,
        description="The source system's unique identifier for this support ticket.",
        aliases=["ticket_ref", "ticket_id", "case_number"],
    ),
    CanonicalField(
        name="ticket_customer_natural_key",
        target_table="support_tickets",
        target_column="customer_id",
        type=FieldType.STRING,
        required=True,
        description="The natural key of the customer who raised this support ticket; resolved to customers.id during transform, not written directly.",
        aliases=["customer_id", "cust_id", "client_id"],
    ),
    CanonicalField(
        name="ticket_subject",
        target_table="support_tickets",
        target_column="subject",
        type=FieldType.STRING,
        required=False,
        description="A short summary of what the support ticket is about.",
        aliases=["subject", "title", "summary"],
    ),
    CanonicalField(
        name="ticket_priority",
        target_table="support_tickets",
        target_column="priority",
        type=FieldType.ENUM,
        required=False,
        description="The urgency level assigned to the support ticket.",
        aliases=["priority", "severity"],
        enum_values=["low", "medium", "high", "urgent"],
    ),
    CanonicalField(
        name="ticket_status",
        target_table="support_tickets",
        target_column="status",
        type=FieldType.ENUM,
        required=False,
        description="The current status of the support ticket.",
        aliases=["status", "ticket_status"],
        enum_values=["open", "pending", "closed"],
    ),
    CanonicalField(
        name="ticket_opened_at",
        target_table="support_tickets",
        target_column="opened_at",
        type=FieldType.DATE,
        required=False,
        description="The date the support ticket was opened.",
        aliases=["opened_at", "open_date"],
    ),
    CanonicalField(
        name="ticket_closed_at",
        target_table="support_tickets",
        target_column="closed_at",
        type=FieldType.DATE,
        required=False,
        description="The date the support ticket was closed, if resolved.",
        aliases=["closed_at", "close_date", "resolved_at"],
    ),
]

_BY_NAME = {field.name: field for field in CANONICAL_FIELDS}


def get_field(name: str) -> CanonicalField:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown canonical field: {name}") from exc


def fields_for_table(target_table: str) -> list[CanonicalField]:
    return [f for f in CANONICAL_FIELDS if f.target_table == target_table]
