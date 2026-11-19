"""Detecting that a deployed pipe or file format no longer matches the configuration."""

from pathlib import Path

import yaml

from partner_factory import drift, sql
from partner_factory.config import Partner, load_partner, load_platform

REPO = Path(__file__).parent.parent
PLATFORM = load_platform(REPO / "platform" / "dev.yml")
PARTNER_FILE = REPO / "partners" / "mercadolocal_br.yml"
PARTNER = load_partner(PARTNER_FILE)


def test_every_pipe_definition_is_taken_from_the_deploy_script() -> None:
    definitions = sql.pipe_definitions(PLATFORM, PARTNER)
    assert set(definitions) == {"STORES_PIPE", "SALES_PIPE"}
    assert definitions["SALES_PIPE"].startswith("COPY INTO MERCADOLOCAL_BR_DEV.RAW.SALES")
    assert definitions["SALES_PIPE"].endswith("ON_ERROR = CONTINUE")
    assert definitions["SALES_PIPE"] in sql.partner_sql(PLATFORM, PARTNER)


def test_a_pipe_only_differs_when_its_statement_does() -> None:
    generated = sql.pipe_definitions(PLATFORM, PARTNER)["SALES_PIPE"]
    reformatted = "  ".join(generated.split()) + ";"
    assert drift.compare_pipe("P", reformatted, generated) is None

    raw = yaml.safe_load(PARTNER_FILE.read_text())
    raw["sources"]["sales"]["csv"]["columns"].append("channel")
    changed = sql.pipe_definitions(PLATFORM, Partner.model_validate(raw))["SALES_PIPE"]
    difference = drift.compare_pipe("P", generated, changed)
    assert difference is not None and "'channel', $8" in difference.detail


def test_a_file_format_is_compared_on_the_options_the_factory_sets() -> None:
    generated = sql.file_format_options(PARTNER.sources["sales"])
    # What SHOW FILE FORMATS reports: the same options plus Snowflake defaults.
    deployed = generated | {"RECORD_DELIMITER": "\n", "COMPRESSION": "AUTO"}
    assert drift.compare_format("F", deployed, generated) is None

    difference = drift.compare_format("F", deployed | {"FIELD_DELIMITER": ";"}, generated)
    assert difference.detail == "FIELD_DELIMITER: deployed ';', generated ','"


def test_format_options_render_as_sql() -> None:
    sales = sql.source_objects("sales", PARTNER.sources["sales"])
    assert sales.file_format_options == [
        "TYPE = CSV",
        "FIELD_DELIMITER = ','",
        "FIELD_OPTIONALLY_ENCLOSED_BY = '\"'",
        "ENCODING = 'UTF8'",
        "SKIP_HEADER = 0",
        "TRIM_SPACE = FALSE",
        "NULL_IF = ()",
        "ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE",
    ]
