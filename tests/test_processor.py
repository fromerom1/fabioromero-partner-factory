"""From a partner mapping to the SQL expressions the dbt project runs with."""

import copy
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from partner_factory import processor
from partner_factory.config import MoneyFormat, Partner, load_partner, load_platform
from test_config import VALID

REPO = Path(__file__).parent.parent
PLATFORM = load_platform(REPO / "platform" / "dev.yml")
PARTNERS = {path.stem: load_partner(path) for path in sorted((REPO / "partners").glob("*.yml"))}


def _fields(partner: str, entity: str) -> dict[str, dict]:
    spec = processor.entity_spec(PLATFORM, PARTNERS[partner], entity)
    return {field["name"]: field for field in spec["fields"]}


def test_types_come_from_the_standard_model_not_from_the_partner() -> None:
    spain = _fields("tiendaviva_es", "sales_transaction")
    assert spain["transaction_ts"]["value"] == (
        "TRY_TO_TIMESTAMP_NTZ(transaction_ts__raw, 'DD/MM/YYYY HH24:MI:SS')"
    )
    assert spain["amount"]["raw"] == "NULLIF(TRIM(src.RECORD['total_cents']::VARCHAR), '')"
    assert spain["amount"]["value"] == (
        "IFF(REGEXP_LIKE(amount__raw, '-?[0-9]+'),"
        " (TRY_TO_DECIMAL(amount__raw, 11, 0) / 100)::NUMBER(11, 2), NULL)"
    )


# --- Amounts: validated against the declared notation, and only then cleaned --------------------

DOLLARS = MoneyFormat(symbol="$", thousands=",", decimal=".")
EUROPEAN = MoneyFormat(symbol="€", thousands=".", decimal=",")
CENTS = MoneyFormat(unit="cents")


@pytest.mark.parametrize(
    ("money", "text"),
    [
        (DOLLARS, "$63.98"),
        (DOLLARS, "63.98"),
        (DOLLARS, "$1,250.00"),
        (DOLLARS, "1250"),
        (DOLLARS, "$ 63.9"),
        (DOLLARS, "-$5.00"),
        (DOLLARS, "1,250,000.50"),
        (EUROPEAN, "63,98"),
        (EUROPEAN, "€1.250,00"),
        (CENTS, "6398"),
        (CENTS, "-100"),
    ],
)
def test_amounts_in_the_declared_notation_are_valid(money: MoneyFormat, text: str) -> None:
    assert re.fullmatch(processor.amount_pattern(money), text)


@pytest.mark.parametrize(
    ("money", "text"),
    [
        (DOLLARS, "63,98"),  # decimal comma: would read as 6398
        (DOLLARS, "1.250,00"),  # European grouping: would read as 1.25
        (DOLLARS, "abc63.98"),
        (DOLLARS, "63.98abc"),
        (DOLLARS, "1,2,3.45"),  # groups that are not thousands
        (DOLLARS, "12,50.00"),
        (DOLLARS, "1e3"),  # scientific notation
        (DOLLARS, "63.987"),  # a third decimal is not rounded away
        (DOLLARS, "$"),
        (DOLLARS, "$$5.00"),
        (DOLLARS, "5."),
        (DOLLARS, ".5"),
        (DOLLARS, "€63.98"),
        (EUROPEAN, "63.98"),
        (EUROPEAN, "1,250.00"),
        (CENTS, "63.98"),
        (CENTS, "6398.5"),
        (CENTS, "1e3"),
        (CENTS, "$6398"),
    ],
)
def test_amounts_in_any_other_notation_are_invalid(money: MoneyFormat, text: str) -> None:
    assert not re.fullmatch(processor.amount_pattern(money), text)


def test_an_amount_is_only_cleaned_after_it_matched_its_notation() -> None:
    brazil = _fields("mercadolocal_br", "sales_transaction")["amount"]["value"]
    assert brazil == (
        "IFF(REGEXP_LIKE(amount__raw, '-?([$] ?)?([0-9]{1,3}(,[0-9]{3})+|[0-9]+)([.][0-9]{1,2})?'),"
        " TRY_TO_DECIMAL(REPLACE(REGEXP_REPLACE(amount__raw, '[^0-9.,-]', ''), ',', ''), 11, 2),"
        " NULL)"
    )
    cleaned = processor._amount(EUROPEAN, "t").split("TRY_TO_DECIMAL(")[1]
    assert cleaned.startswith(
        "REPLACE(REPLACE(REGEXP_REPLACE(t, '[^0-9.,-]', ''), '.', ''), ',', '.')"
    )


# --- Time zones ---------------------------------------------------------------------------------


def test_local_times_use_the_zone_of_the_store_when_one_is_declared() -> None:
    assert processor.timezone_sql(PARTNERS["mercadolocal_br"]) == "'America/Sao_Paulo'"
    assert processor.timezone_sql(PARTNERS["tiendaviva_es"]) == (
        "DECODE(store_id, 'TV-0047', 'Atlantic/Canary', 'Europe/Madrid')"
    )


def test_fields_the_partner_does_not_send_are_typed_nulls_or_constants() -> None:
    brazil = _fields("mercadolocal_br", "sales_transaction")
    assert brazil["customer_ref"] == {
        "name": "customer_ref",
        "required": False,
        "raw": "NULL::VARCHAR",
        "value": "NULL::VARCHAR",
    }
    assert brazil["status"]["raw"] == "'COMPLETED'"
    assert _fields("mercadolocal_br", "store")["is_active"]["value"] == "NULL::BOOLEAN"


def test_customer_reference_is_a_hash_keyed_with_the_partner_secret() -> None:
    value = _fields("tiendaviva_es", "sales_transaction")["customer_ref"]["value"]
    assert value == (
        "SHA2(CONCAT((SELECT KEY_VALUE FROM TIENDAVIVA_ES_DEV.RAW.PSEUDONYMIZATION_KEY), '|', "
        "REPLACE(UPPER(customer_ref__raw), ' ', '')), 256)"
    )


def test_csv_sources_drop_the_header_and_flag_surplus_fields() -> None:
    sales = processor.entity_spec(PLATFORM, PARTNERS["mercadolocal_br"], "sales_transaction")
    assert sales["is_header"] == (
        "(src.FILE_ROW_NUMBER = 1"
        " AND LOWER(TRIM(src.RECORD['store_token']::VARCHAR)) = 'store_token')"
    )
    assert sales["has_extra_field"] == "src.RECORD['_extra_field']::VARCHAR IS NOT NULL"
    assert sales["key"] == ["store_id", "transaction_id"]

    orders = processor.entity_spec(PLATFORM, PARTNERS["tiendaviva_es"], "sales_transaction")
    assert (orders["is_header"], orders["has_extra_field"]) == ("FALSE", "FALSE")


def test_exploded_sources_read_fields_from_each_element() -> None:
    shops = processor.entity_spec(PLATFORM, PARTNERS["tiendaviva_es"], "store")
    assert shops["explode"] == "src.RECORD['shops']"
    assert shops["fields"][0]["raw"] == "NULLIF(TRIM(item.VALUE['code']::VARCHAR), '')"
    assert shops["batch_date"] == (
        "TRY_TO_DATE(REGEXP_SUBSTR(src.SOURCE_FILE, 'shops_([0-9]{6})[.]json', 1, 1, 'e', 1),"
        " 'YYYYMM')"
    )


def test_a_value_map_translates_partner_statuses() -> None:
    config = copy.deepcopy(VALID)
    config["standard_model"]["sales_transaction"]["fields"]["status"] = {
        "from": "state",
        "value_map": {"PAID": "COMPLETED", "MONEY BACK": "REFUNDED"},
    }
    spec = processor.entity_spec(PLATFORM, Partner.model_validate(config), "sales_transaction")
    status = next(field for field in spec["fields"] if field["name"] == "status")
    assert status["value"] == ("DECODE(status__raw, 'PAID', 'COMPLETED', 'MONEY BACK', 'REFUNDED')")


# Every option of a mapping ends up inside a SQL expression that dbt runs. None of them is free
# text: each has a constrained type, so a value that could close a literal never gets this far.
HOSTILE = "x'; DROP DATABASE VICTIM; --"
FIELD = "standard_model.sales_transaction.fields"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (f"{FIELD}.transaction_id", {"from": HOSTILE}),
        (f"{FIELD}.transaction_id", {"from": "id", "matches": HOSTILE}),
        (f"{FIELD}.transaction_id", {"from": "id\nDROP DATABASE VICTIM"}),
        (f"{FIELD}.transaction_ts", {"from": "created", "format": HOSTILE}),
        (f"{FIELD}.transaction_ts", {"from": "created", "format": "YYYY\\'"}),
        (f"{FIELD}.status", {"constant": HOSTILE}),
        (f"{FIELD}.status", {"from": "s", "value_map": {HOSTILE: "COMPLETED"}}),
        (f"{FIELD}.status", {"from": "s", "value_map": {"PAID": HOSTILE}}),
        ("standard_model.store.explode", HOSTILE),
        ("standard_model.store.source", HOSTILE),
        ("sources.orders.batch_date.regex", f"orders_([0-9]{{8}}){HOSTILE}"),
        ("sources.orders.batch_date.format", HOSTILE),
    ],
)
def test_no_mapping_option_can_carry_sql(path: str, value: object) -> None:
    config = copy.deepcopy(VALID)
    *parents, key = path.split(".")
    node = config
    for parent in parents:
        node = node[parent]
    node[key] = value
    with pytest.raises(ValidationError):
        Partner.model_validate(config)


def test_dbt_gets_the_readers_and_the_protection_of_customer_ref() -> None:
    variables = processor.dbt_vars(PLATFORM, PARTNERS["tiendaviva_es"])
    assert variables["reader_roles"] == [
        "PARTNER_FACTORY_DEV_ANALYST",
        "PARTNER_FACTORY_DEV_FRAUD_CREDIT",
    ]
    assert variables["customer_ref_policy"] == "TIENDAVIVA_ES_DEV.PROCESSED.CUSTOMER_REF_MASK"
    assert variables["pii_tag"] == "TIENDAVIVA_ES_DEV.RAW.PII"
    assert variables["customer_ref_tag_value"] == "PSEUDONYM"
