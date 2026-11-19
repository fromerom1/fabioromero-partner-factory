"""Partner configuration: the shipped files are valid and the mistakes that matter are caught."""

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from partner_factory.config import Partner, load_partner, load_platform

REPO = Path(__file__).parent.parent
PARTNER_FILES = sorted((REPO / "partners").glob("*.yml"))

# A small valid partner: one JSON source feeding both standard entities.
VALID = {
    "partner_code": "ACME_MX",
    "display_name": "Acme",
    "country_code": "MX",
    "currency_code": "MXN",
    "timezone": "America/Mexico_City",
    "sources": {
        "orders": {
            "description": "Orders",
            "format": "json",
            "file_pattern": "orders_[0-9]{8}[.]json",
            "example_file": "orders_20260101.json",
            "batch_date": {"regex": "orders_([0-9]{8})", "format": "YYYYMMDD"},
        }
    },
    "standard_model": {
        "store": {
            "source": "orders",
            "fields": {"store_id": {"from": "shop.id"}, "store_name": {"from": "shop.name"}},
        },
        "sales_transaction": {
            "source": "orders",
            "fields": {
                "transaction_id": {"from": "id"},
                "store_id": {"from": "shop.id"},
                "transaction_ts": {"from": "created", "format": "YYYY-MM-DD HH24:MI:SS"},
                "amount": {"from": "total", "money": {}},
                "status": {"constant": "COMPLETED"},
            },
        },
    },
}


def _changed(path: str, value: object) -> dict:
    """VALID with one value replaced; ``path`` is dotted, and None removes the key."""
    config = copy.deepcopy(VALID)
    *parents, key = path.split(".")
    node = config
    for parent in parents:
        node = node[parent]
    if value is None:
        del node[key]
    else:
        node[key] = value
    return config


def _rejects(path: str, value: object, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Partner.model_validate(_changed(path, value))


@pytest.mark.parametrize("path", PARTNER_FILES, ids=lambda path: path.stem)
def test_shipped_partner_configs_are_valid(path: Path) -> None:
    assert load_partner(path).partner_code == path.stem.upper()


@pytest.mark.parametrize("path", sorted((REPO / "platform").glob("*.yml")), ids=lambda p: p.stem)
def test_shipped_platform_files_are_valid(path: Path) -> None:
    assert load_platform(path).environment == path.stem.upper()


def test_the_reference_configuration_is_valid() -> None:
    assert Partner.model_validate(VALID).partner_code == "ACME_MX"


def test_unknown_keys_are_rejected() -> None:
    _rejects("time_zone", "UTC", "Extra inputs are not permitted")


def test_two_sources_cannot_claim_the_same_file() -> None:
    everything = VALID["sources"]["orders"] | {"file_pattern": ".*", "example_file": "x_20260101"}
    everything["batch_date"] = {"regex": "x_([0-9]{8})", "format": "YYYYMMDD"}
    _rejects("sources.everything", everything, "also matches 'orders_20260101.json'")


def test_pattern_must_match_its_example_file() -> None:
    _rejects("sources.orders.example_file", "orders.json", "does not match example_file")


def test_csv_sources_must_declare_their_columns() -> None:
    _rejects("sources.orders.format", "csv", "'csv' block is required")


def test_batch_date_must_be_readable_from_the_example_file() -> None:
    _rejects("sources.orders.batch_date.regex", "orders_([0-9]{6})", "finds no YYYYMMDD date")
    _rejects("sources.orders.batch_date.regex", "orders_[0-9]{8}", "exactly one capturing group")


def test_values_that_could_break_out_of_generated_sql_are_rejected() -> None:
    _rejects("sources.orders.file_pattern", "x'; DROP DATABASE P; --", "file_pattern")
    _rejects("partner_code", "ACME; DROP", "partner_code")
    _rejects("timezone", "UTC'; DROP DATABASE P; --", "timezone")


def test_file_must_be_named_after_its_partner_code(tmp_path: Path) -> None:
    path = tmp_path / "other_name.yml"
    path.write_text(yaml.safe_dump(VALID))
    with pytest.raises(ValueError, match="acme_mx.yml"):
        load_partner(path)


# --- Mapping to the standard model ------------------------------------------------------------

FIELDS = "standard_model.sales_transaction.fields"
AMOUNT = {"from": "total", "money": {}}


def test_required_standard_fields_must_be_mapped() -> None:
    _rejects(f"{FIELDS}.amount", None, "required fields not mapped: amount")


def test_only_standard_fields_can_be_mapped() -> None:
    _rejects(f"{FIELDS}.discount", {"from": "discount"}, "not a standard field")


def test_a_mapping_must_use_a_declared_source_and_its_columns() -> None:
    _rejects("standard_model.store.source", "shops", "'shops' is not one of the sources")
    shipped = yaml.safe_load((REPO / "partners" / "mercadolocal_br.yml").read_text())
    shipped["standard_model"]["store"]["fields"]["store_name"] = {"from": "name"}
    with pytest.raises(ValidationError, match="'name' is not a column of 'stores'"):
        Partner.model_validate(shipped)


def test_field_options_must_fit_the_kind_of_field() -> None:
    _rejects(f"{FIELDS}.transaction_ts", {"from": "created"}, "needs a 'format'")
    _rejects(f"{FIELDS}.amount", {"from": "total"}, "needs 'money'")
    _rejects(f"{FIELDS}.amount", AMOUNT | {"transforms": ["lower"]}, "does not apply")
    _rejects(f"{FIELDS}.amount", AMOUNT | {"transforms": ["sql"]}, "Input should be")
    _rejects(f"{FIELDS}.store_id", {"from": "shop.id", "money": {}}, "only applies to amount")
    _rejects(f"{FIELDS}.status", {"constant": "PAID"}, "unknown status")
    _rejects(f"{FIELDS}.status", {"from": "s", "value_map": {"PAID": "DONE"}}, "unknown status")
    _rejects(f"{FIELDS}.amount", AMOUNT | {"constant": "1"}, "exactly one of")


def test_money_formats_must_be_unambiguous() -> None:
    same = {"from": "total", "money": {"thousands": ",", "decimal": ","}}
    _rejects(f"{FIELDS}.amount", same, "must differ")
    cents = {"from": "total", "money": {"unit": "cents", "symbol": "$"}}
    _rejects(f"{FIELDS}.amount", cents, "plain integers")
    _rejects(f"{FIELDS}.amount", {"from": "total", "money": {"symbol": "'"}}, "Input should be")


def test_store_time_zones_must_be_real_zones_of_plain_store_ids() -> None:
    assert Partner.model_validate(_changed("store_timezones", {"S-1": "Atlantic/Canary"}))
    _rejects("store_timezones", {"S-1": "Atlantic/Atlantis"}, "unknown IANA time zone")
    _rejects("store_timezones", {"S'; DROP DATABASE P; --": "UTC"}, "store_timezones")


def test_customer_data_can_only_leave_raw_pseudonymized() -> None:
    _rejects(f"{FIELDS}.customer_ref", {"from": "customer.id"}, "must end with the 'pseudonymize'")
    _rejects(
        f"{FIELDS}.customer_ref",
        {"from": "customer.id", "transforms": ["pseudonymize", "upper"]},
        "must end with the 'pseudonymize'",
    )
    _rejects(f"{FIELDS}.store_id", {"from": "x", "transforms": ["pseudonymize"]}, "does not apply")


def test_the_transaction_key_must_include_the_transaction_id() -> None:
    _rejects("standard_model.sales_transaction.key", ["store_id"], "must include transaction_id")
