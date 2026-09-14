"""Deterministic validation: Pandera schema contracts + hand-written
business rules. Neither touches the database — referential integrity
and duplicate detection need DB access and live in app.loader instead.
"""

from datetime import date

import pandera.pandas as pa

from app.canonical import FieldType, fields_for_table
from app.record_builder import LINKING_FIELDS, FieldError

_EXPECTED_TYPE_BY_FIELD_TYPE: dict[FieldType, type] = {
    FieldType.STRING: str,
    FieldType.EMAIL: str,
    FieldType.PHONE: str,
    FieldType.ENUM: str,
    FieldType.CURRENCY_CODE: str,
    FieldType.DATE: date,
    FieldType.DATETIME: date,
    FieldType.INTEGER: int,
    FieldType.MONEY_MINOR_UNITS: int,
}

_SCHEMA_CACHE: dict[str, pa.DataFrameSchema] = {}


def _schema_for_table(target_table: str) -> pa.DataFrameSchema:
    columns = {}
    for field in fields_for_table(target_table):
        if field.name in LINKING_FIELDS:
            continue
        expected_type = _EXPECTED_TYPE_BY_FIELD_TYPE[field.type]
        columns[field.target_column] = pa.Column(
            object,
            nullable=not field.required,
            checks=pa.Check(
                lambda s, t=expected_type: s.dropna().map(lambda v: isinstance(v, t)).all()
            ),
        )
    return pa.DataFrameSchema(columns)


def _get_schema(target_table: str) -> pa.DataFrameSchema:
    if target_table not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[target_table] = _schema_for_table(target_table)
    return _SCHEMA_CACHE[target_table]


def validate_schema(target_table: str, values: dict) -> list[FieldError]:
    import pandas as pd

    schema = _get_schema(target_table)
    # Only check columns `values` actually has an entry for -- not every
    # column ever registered for this table. A canonical field absent
    # from `values` means either the confirmed spec never mapped it (an
    # older spec, from before this field existed — not this record's
    # problem to fail on) or app.record_builder already reported
    # MISSING_REQUIRED for it and deliberately left it out of `values`
    # (see apply_spec_to_row's docstring) — either way, re-flagging a
    # column that plain isn't present as a Pandera nullable violation
    # would be a second, wrong error for the same thing, or (for a field
    # the current spec never mapped at all) a false positive entirely.
    # Confirmed via Day 6's schema-evolution work: adding a new required
    # canonical field broke every old batch's reload until this scoped
    # to `values`'s own keys.
    checked_columns = [name for name in schema.columns if name in values]
    if not checked_columns:
        return []

    # Pandera errors on a schema-declared column that's entirely absent
    # from the dataframe (a "column_in_dataframe" failure), independent
    # of that column's nullable setting -- so it's not enough to just
    # fill absent columns with None; the schema itself must be narrowed
    # to only the columns actually being checked this call.
    subset_schema = pa.DataFrameSchema({name: schema.columns[name] for name in checked_columns})
    row = {name: [values.get(name)] for name in checked_columns}
    # dtype=object is required here: pandas auto-infers a concrete dtype
    # (e.g. int64) from a single-value column, which then mismatches this
    # schema's declared `object` dtype for every column regardless of the
    # isinstance() check passing — a dtype-vs-dtype failure, not a value
    # failure.
    df = pd.DataFrame(row, dtype=object)
    try:
        subset_schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        errors = []
        for _, failure in exc.failure_cases.iterrows():
            errors.append(
                FieldError(
                    field=str(failure.get("column") or "unknown"),
                    code="SCHEMA_VALIDATION_FAILED",
                    raw_value=str(failure.get("failure_case")),
                )
            )
        return errors
    return []


def validate_business_rules(target_table: str, values: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    if target_table != "invoices":
        return errors

    issue_date = values.get("issue_date")
    due_date = values.get("due_date")
    amount = values.get("amount_minor_units")

    if issue_date is not None and issue_date > date.today():
        errors.append(
            FieldError(field="issue_date", code="BUSINESS_RULE_FUTURE_DATE", raw_value=str(issue_date))
        )
    if amount is not None and amount < 0:
        errors.append(
            FieldError(field="amount_minor_units", code="BUSINESS_RULE_NEGATIVE_AMOUNT", raw_value=str(amount))
        )
    if issue_date is not None and due_date is not None and due_date < issue_date:
        errors.append(
            FieldError(field="due_date", code="BUSINESS_RULE_DUE_BEFORE_ISSUE", raw_value=str(due_date))
        )
    return errors
