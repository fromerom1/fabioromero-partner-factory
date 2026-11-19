"""Partner configuration: the shipped files are valid and the mistakes that matter are caught."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from partner_factory.config import Partner, load_partner, load_platform

REPO = Path(__file__).parent.parent
PARTNER_FILES = sorted((REPO / "partners").glob("*.yml"))


def _config(**sources: dict) -> dict:
    return {
        "partner_code": "ACME_MX",
        "display_name": "Acme",
        "country_code": "MX",
        "currency_code": "MXN",
        "timezone": "America/Mexico_City",
        "sources": sources,
    }


def _json_source(pattern: str, example: str) -> dict:
    return {"description": "d", "format": "json", "file_pattern": pattern, "example_file": example}


@pytest.mark.parametrize("path", PARTNER_FILES, ids=lambda path: path.stem)
def test_shipped_partner_configs_are_valid(path: Path) -> None:
    assert load_partner(path).partner_code == path.stem.upper()


@pytest.mark.parametrize("path", sorted((REPO / "platform").glob("*.yml")), ids=lambda p: p.stem)
def test_shipped_platform_files_are_valid(path: Path) -> None:
    assert load_platform(path).environment == path.stem.upper()


def test_unknown_keys_are_rejected() -> None:
    config = _config(orders=_json_source("orders_[0-9]+[.]json", "orders_1.json"))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Partner.model_validate(config | {"time_zone": "UTC"})


def test_two_sources_cannot_claim_the_same_file() -> None:
    config = _config(
        orders=_json_source("orders_[0-9]+[.]json", "orders_1.json"),
        everything=_json_source(".*[.]json", "shops.json"),
    )
    with pytest.raises(ValidationError, match="also matches 'orders_1.json'"):
        Partner.model_validate(config)


def test_pattern_must_match_its_example_file() -> None:
    with pytest.raises(ValidationError, match="does not match example_file"):
        Partner.model_validate(
            _config(orders=_json_source("orders_[0-9]{8}[.]json", "orders.json"))
        )


def test_csv_sources_must_declare_their_columns() -> None:
    source = _json_source("sales_[0-9]+[.]csv", "sales_1.csv") | {"format": "csv"}
    with pytest.raises(ValidationError, match="'csv' block is required"):
        Partner.model_validate(_config(sales=source))


def test_values_that_could_break_out_of_generated_sql_are_rejected() -> None:
    config = _config(orders=_json_source("x'; DROP DATABASE P; --", "x"))
    with pytest.raises(ValidationError, match="file_pattern"):
        Partner.model_validate(config)
    with pytest.raises(ValidationError, match="partner_code"):
        Partner.model_validate(config | {"partner_code": "ACME; DROP"})


def test_file_must_be_named_after_its_partner_code(tmp_path: Path) -> None:
    path = tmp_path / "other_name.yml"
    path.write_text(yaml.safe_dump(_config(orders=_json_source("o[.]json", "o.json"))))
    with pytest.raises(ValueError, match="acme_mx.yml"):
        load_partner(path)
