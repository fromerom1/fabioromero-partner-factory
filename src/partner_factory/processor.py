"""Turns the standard-model mapping of a partner into the variables the dbt project runs with.

The dbt project is generic: its models are written once per standard entity and know nothing
about any partner. What changes per partner is this set of variables, mostly SQL expressions
compiled from the partner file. Like sql.py, this module is pure: configuration in, text out.

Every expression is built from constrained configuration values, and text only becomes a SQL
literal through ``sql.literal``.
"""

from __future__ import annotations

from partner_factory.config import EntityMapping, FieldMapping, Partner, Platform, Source
from partner_factory.sql import EXTRA_FIELD_KEY, PSEUDONYMIZATION_KEY_TABLE, literal
from partner_factory.standard_model import ENTITIES, SALE_STATUSES, SQL_TYPES, StandardField

# Transformations that rewrite the text of a value before it is typed.
_TEXT_TRANSFORMS = {
    "lower": "LOWER({})",
    "upper": "UPPER({})",
    "remove_spaces": "REPLACE({}, ' ', '')",
    # Keeps digits, separators and sign: '$63.98' and 'R$ 63.98' both become '63.98'.
    "strip_currency_symbol": "REGEXP_REPLACE({}, '[^0-9.,+-]', '')",
    "remove_thousands_separator": "REPLACE({}, ',', '')",
}


def _path(base: str, path: str) -> str:
    """``order.id`` under ``src.RECORD`` becomes ``src.RECORD['order']['id']``."""
    return base + "".join(f"[{literal(key)}]" for key in path.split("."))


def _raw_text(base: str, mapping: FieldMapping | None) -> str:
    """The value as the partner sent it, as text. Blank and JSON null both count as missing."""
    if mapping is None:
        return "NULL::VARCHAR"
    if mapping.constant is not None:
        return literal(mapping.constant)
    return f"NULLIF(TRIM({_path(base, mapping.source)}::VARCHAR), '')"


def _typed_value(
    field: StandardField, mapping: FieldMapping | None, column: str, key_table: str
) -> str:
    """The standard value, computed from the raw text column. NULL when it cannot be converted.

    Every conversion is a TRY_ function or a guarded expression: a bad value yields NULL, which
    the models report as ``invalid_<field>``, and never fails the run.
    """
    if mapping is None:
        return f"NULL::{SQL_TYPES[field.kind]}"

    text = column
    for transform in mapping.transforms:
        if transform in _TEXT_TRANSFORMS:
            text = _TEXT_TRANSFORMS[transform].format(text)

    match field.kind:
        case "text":
            if mapping.matches:
                return f"IFF(REGEXP_LIKE({text}, {literal(mapping.matches)}), {text}, NULL)"
            return text
        case "boolean":
            return f"TRY_TO_BOOLEAN({text})"
        case "timestamp":
            return f"TRY_TO_TIMESTAMP_NTZ({text}, {literal(mapping.format)})"
        case "amount":
            if "cents_to_units" in mapping.transforms:
                return f"(TRY_TO_DECIMAL({text}, 11, 0) / 100)::NUMBER(11, 2)"
            return f"TRY_TO_DECIMAL({text}, 11, 2)"
        case "status":
            if mapping.value_map:
                pairs = ", ".join(
                    f"{literal(partner_value)}, {literal(standard_value)}"
                    for partner_value, standard_value in mapping.value_map.items()
                )
                return f"DECODE({text}, {pairs})"
            statuses = ", ".join(literal(status) for status in SALE_STATUSES)
            return f"IFF(UPPER({text}) IN ({statuses}), UPPER({text}), NULL)"
        case "currency":
            return f"IFF(REGEXP_LIKE(UPPER({text}), '[A-Z]{{3}}'), UPPER({text}), NULL)"
        case "pseudonym":
            # A keyed hash: without the key of this partner the reference cannot be recomputed
            # from the identifier, and the same customer is unlinkable across partners.
            return f"SHA2(CONCAT((SELECT KEY_VALUE FROM {key_table}), '|', {text}), 256)"


def _header_predicate(source: Source) -> str:
    """True for the header line of a CSV file, which is neither a valid nor a rejected record."""
    if source.csv is None:
        return "FALSE"
    first = source.csv.columns[0]
    value = f"LOWER(TRIM({_path('src.RECORD', first)}::VARCHAR))"
    return f"(src.FILE_ROW_NUMBER = 1 AND {value} = {literal(first)})"


def entity_spec(platform: Platform, partner: Partner, entity_name: str) -> dict:
    """What the generic models need to read one standard entity from the partner's raw table."""
    entity = ENTITIES[entity_name]
    mapping: EntityMapping = getattr(partner.standard_model, entity_name)
    source = partner.sources[mapping.source]
    key_table = f"{platform.database(partner)}.RAW.{PSEUDONYMIZATION_KEY_TABLE}"
    # With `explode`, each element of the array is a row and fields are read from the element.
    base = "item.VALUE" if mapping.explode else "src.RECORD"

    fields = []
    for field in entity.fields:
        field_mapping = mapping.fields.get(field.name)
        fields.append(
            {
                "name": field.name,
                "required": field.required,
                "raw": _raw_text(base, field_mapping),
                "value": _typed_value(field, field_mapping, f"{field.name}__raw", key_table),
            }
        )

    spec = {
        "raw_table": mapping.source.upper(),
        "explode": _path("src.RECORD", mapping.explode) if mapping.explode else None,
        "is_header": _header_predicate(source),
        "has_extra_field": (
            f"{_path('src.RECORD', EXTRA_FIELD_KEY)}::VARCHAR IS NOT NULL"
            if source.csv
            else "FALSE"
        ),
        "batch_date": (
            f"TRY_TO_DATE(REGEXP_SUBSTR(src.SOURCE_FILE, {literal(source.batch_date.regex)},"
            f" 1, 1, 'e', 1), {literal(source.batch_date.format)})"
        ),
        "fields": fields,
    }
    if entity_name == "sales_transaction":
        spec["key"] = list(partner.standard_model.sales_transaction.key)
    return spec


def dbt_vars(platform: Platform, partner: Partner) -> dict:
    """The complete ``--vars`` of a dbt invocation for one partner."""
    return {
        "partner_code": partner.partner_code,
        "country_code": partner.country_code,
        "currency_code": partner.currency_code,
        "timezone": partner.timezone,
        **{name: entity_spec(platform, partner, name) for name in ENTITIES},
    }
