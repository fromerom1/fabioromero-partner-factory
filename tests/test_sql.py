"""Generated SQL: what the loader of a partner looks like, and what it must never contain."""

import io
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from snowflake.connector.util_text import split_statements

from partner_factory import sql
from partner_factory.config import Partner, load_partner, load_platform

REPO = Path(__file__).parent.parent
PLATFORM = load_platform(REPO / "platform" / "dev.yml")
PARTNER_FILES = {path.stem: path for path in sorted((REPO / "partners").glob("*.yml"))}
PARTNERS = {name: load_partner(path) for name, path in PARTNER_FILES.items()}

# Values a careless or malicious configuration could carry in a free-text field.
HOSTILE_TEXT = [
    "x\nDROP DATABASE VICTIM;",
    "x\r\nDROP DATABASE VICTIM;",
    "x\u2028DROP DATABASE VICTIM;",
    "x\\'; DROP DATABASE VICTIM; --",
    "x'; DROP DATABASE VICTIM; --",
    "x; DROP DATABASE VICTIM",
    "x */ DROP DATABASE VICTIM; /*",
]


def _with(path: Path, field: str, value: str) -> dict:
    """The raw configuration of a shipped partner with one field replaced."""
    raw = yaml.safe_load(path.read_text())
    first_source = next(iter(raw["sources"].values()))
    if field == "display_name":
        raw["display_name"] = value
    elif field == "description":
        first_source["description"] = value
    elif field == "csv_column":
        first_source["csv"]["columns"][0] = value
    return raw


def _statement_kinds(script: str) -> list[str]:
    """What the runner would execute: the leading keywords of every statement, in order."""
    statements = split_statements(io.StringIO(script), remove_comments=True)
    return [" ".join(text.split()[:3]).upper() for text, _ in statements if text.strip()]


@pytest.mark.parametrize("name", PARTNERS)
def test_every_source_gets_a_table_and_an_auto_ingest_pipe(name: str) -> None:
    partner = PARTNERS[name]
    script = sql.partner_sql(PLATFORM, partner)
    database = f"{partner.partner_code}_DEV"

    assert f"CREATE DATABASE IF NOT EXISTS {database}" in script
    assert f"URL = 's3://{PLATFORM.aws.bucket}/{name}/landing/'" in script
    for source in partner.sources:
        table = f"{database}.RAW.{source.upper()}"
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in script
        assert f"CREATE PIPE IF NOT EXISTS {table}_PIPE\n    AUTO_INGEST = TRUE" in script
    assert script.count("ON_ERROR = CONTINUE") == len(partner.sources)


@pytest.mark.parametrize("name", PARTNERS)
def test_deploying_is_never_destructive(name: str) -> None:
    """Replacing a pipe drops its load history and replacing a table drops its data."""
    script = sql.partner_sql(PLATFORM, PARTNERS[name]).upper()
    assert "OR REPLACE" not in script
    assert "DROP " not in script


def test_csv_records_keep_the_column_order_of_the_configuration() -> None:
    stores = sql.source_objects("stores", PARTNERS["mercadolocal_br"].sources["stores"])
    assert stores.record_expression == (
        "OBJECT_CONSTRUCT_KEEP_NULL("
        "'store_group', $1, 'store_token', $2, 'store_name', $3, '_extra_field', $4)"
    )


def test_csv_records_keep_the_first_field_beyond_the_configured_columns() -> None:
    """Without it, a line with too many fields would lose the surplus without a trace."""
    sales = PARTNERS["mercadolocal_br"].sources["sales"]
    expression = sql.source_objects("sales", sales).record_expression
    assert expression.endswith(f"'user_role', $7, '{sql.EXTRA_FIELD_KEY}', $8)")
    # Column names must start with a letter, so no configured column can shadow the key.
    with pytest.raises(ValidationError):
        Partner.model_validate(
            _with(PARTNER_FILES["mercadolocal_br"], "csv_column", "_extra_field")
        )


def test_json_records_are_loaded_as_delivered() -> None:
    orders = sql.source_objects("orders", PARTNERS["tiendaviva_es"].sources["orders"])
    assert orders.record_expression == "$1"
    assert orders.file_format_options == ["TYPE = JSON"]


def test_bootstrap_refuses_a_private_key() -> None:
    # Assembled from parts so secret scanners do not flag this file.
    marker = "PRIVATE" + " KEY"
    pem = f"-----BEGIN {marker}-----\n{'A' * 400}\n-----END {marker}-----\n"
    with pytest.raises(ValueError, match="expected a PEM public key"):
        sql.platform_sql(PLATFORM, pem)


@pytest.mark.parametrize("name", PARTNERS)
@pytest.mark.parametrize("field", ["display_name", "description"])
@pytest.mark.parametrize("value", HOSTILE_TEXT)
def test_free_text_cannot_add_or_change_statements(name: str, field: str, value: str) -> None:
    """A hostile value is either rejected or stays inside its literal: same statements as before."""
    baseline = _statement_kinds(sql.partner_sql(PLATFORM, PARTNERS[name]))
    try:
        partner = Partner.model_validate(_with(PARTNER_FILES[name], field, value))
    except ValidationError:
        return
    assert _statement_kinds(sql.partner_sql(PLATFORM, partner)) == baseline


def test_quotes_in_free_text_are_escaped_and_backslashes_refused() -> None:
    raw = _with(PARTNER_FILES["tiendaviva_es"], "display_name", "L'Illa; Diagonal")
    script = sql.partner_sql(PLATFORM, Partner.model_validate(raw))
    assert "COMMENT = 'L''Illa; Diagonal: partner factory, DEV';" in script

    with pytest.raises(ValidationError, match="backslash"):
        Partner.model_validate(_with(PARTNER_FILES["tiendaviva_es"], "display_name", "a\\b"))
    # The escaping function refuses on its own, whatever the models allowed through.
    for text in ("a\\'b", "a\nb"):
        with pytest.raises(ValueError, match="cannot be rendered"):
            sql.literal(text)


@pytest.mark.parametrize("name", PARTNERS)
def test_readers_are_never_granted_raw_or_whole_schemas(name: str) -> None:
    script = sql.partner_sql(PLATFORM, PARTNERS[name])
    database = PLATFORM.database(PARTNERS[name])
    grants = [line for line in script.splitlines() if line.startswith("GRANT ")]
    # Entering the database and two schemas; the tables themselves are granted one by one by dbt.
    assert sorted(grants) == sorted(
        f"GRANT USAGE ON {target} TO ROLE {role};"
        for role in PLATFORM.snowflake.reader_roles
        for target in (
            f"DATABASE {database}",
            f"SCHEMA {database}.PROCESSED",
            f"SCHEMA {database}.ANALYTICS",
        )
    )


def test_the_masking_policy_shows_customer_references_to_two_roles_only() -> None:
    script = sql.partner_sql(PLATFORM, PARTNERS["tiendaviva_es"])
    policy = "TIENDAVIVA_ES_DEV.PROCESSED.CUSTOMER_REF_MASK"
    assert f"CREATE MASKING POLICY IF NOT EXISTS {policy}\n" in script
    in_session = re.findall(r"IS_ROLE_IN_SESSION\('(\w+)'\)", script)
    sf = PLATFORM.snowflake
    assert in_session == [sf.pipeline_role, sf.fraud_credit_role]


def test_raw_records_are_tagged_only_for_sources_with_personal_data() -> None:
    tag = "SET TAG {db}.RAW.PII = 'CUSTOMER_DATA'"
    spain = sql.partner_sql(PLATFORM, PARTNERS["tiendaviva_es"])
    assert (
        "ALTER TABLE TIENDAVIVA_ES_DEV.RAW.ORDERS MODIFY COLUMN RECORD\n    "
        + tag.format(db="TIENDAVIVA_ES_DEV")
    ) in spain
    assert spain.count("'CUSTOMER_DATA';") == 1
    assert "'CUSTOMER_DATA';" not in sql.partner_sql(PLATFORM, PARTNERS["mercadolocal_br"])


def test_bootstrap_creates_the_reader_roles() -> None:
    marker = "PUBLIC" + " KEY"
    pem = f"-----BEGIN {marker}-----\n{'A' * 400}\n-----END {marker}-----\n"
    script = sql.platform_sql(PLATFORM, pem)
    for role in PLATFORM.snowflake.reader_roles:
        assert f"CREATE ROLE IF NOT EXISTS {role}\n" in script
        assert f"GRANT USAGE ON WAREHOUSE {PLATFORM.snowflake.warehouse} TO ROLE {role};" in script
