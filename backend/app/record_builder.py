"""Applies a confirmed mapping spec to one raw row.

Groups the spec's per-field transforms by target table (a single row can
produce more than one canonical record — a billing row produces both an
invoices record and an accounts record) and runs each field's transform
function. MISSING_REQUIRED is caught here, not in app.validation,
because it's a direct property of "did the transform produce a value
for a required field" — no business logic involved.

The three "linking" fields (target_column="customer_id" on
accounts/invoices/support_tickets) never land in a record's `values`
dict — app.loader resolves them to a real customers.id via a DB lookup,
since that's the only place with database access in this pipeline.
"""

from pydantic import BaseModel

from app.canonical import get_field
from app.transforms import map_enum, parse_date, to_int, to_minor_units, trim

LINKING_FIELDS = {
    "account_customer_natural_key",
    "invoice_customer_natural_key",
    "ticket_customer_natural_key",
}

_TRANSFORM_FUNCTIONS = {
    "trim": trim,
    "parse_date": parse_date,
    "to_int": to_int,
    "to_minor_units": to_minor_units,
}


class FieldError(BaseModel):
    field: str
    code: str
    raw_value: str | None


class BuiltRecord(BaseModel):
    target_table: str
    values: dict
    customer_natural_key: str | None = None
    errors: list[FieldError] = []


def apply_spec_to_row(spec_json: dict, raw_row: dict) -> list[BuiltRecord]:
    values_by_table: dict[str, dict] = {}
    customer_key_by_table: dict[str, str | None] = {}
    errors_by_table: dict[str, list[FieldError]] = {}

    for canonical_field_name, entry in spec_json.items():
        field = get_field(canonical_field_name)
        raw_value = raw_row.get(entry["source_column"])
        function_name = entry["transform"]["function"]

        if function_name == "map_enum":
            result = map_enum(raw_value, field.enum_values or [])
        else:
            result = _TRANSFORM_FUNCTIONS[function_name](raw_value)

        values_by_table.setdefault(field.target_table, {})
        errors_by_table.setdefault(field.target_table, [])
        customer_key_by_table.setdefault(field.target_table, None)

        if result.error_code is not None:
            errors_by_table[field.target_table].append(
                FieldError(field=canonical_field_name, code=result.error_code, raw_value=raw_value)
            )
            continue

        if result.value is None and field.required:
            errors_by_table[field.target_table].append(
                FieldError(field=canonical_field_name, code="MISSING_REQUIRED", raw_value=raw_value)
            )
            continue

        if canonical_field_name in LINKING_FIELDS:
            customer_key_by_table[field.target_table] = result.value
        else:
            values_by_table[field.target_table][field.target_column] = result.value

    return [
        BuiltRecord(
            target_table=table,
            values=values,
            customer_natural_key=customer_key_by_table[table],
            errors=errors_by_table[table],
        )
        for table, values in values_by_table.items()
    ]
