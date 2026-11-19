"""Turns configuration into Snowflake SQL.

The functions here are pure: configuration in, SQL text out. They never connect to Snowflake,
which keeps the generated SQL testable and lets every command offer ``--dry-run``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jinja2 import Environment, PackageLoader, StrictUndefined

from partner_factory.config import Partner, Platform, Source

# Key of the raw record that holds the first field beyond the configured CSV columns. Column
# names start with a letter, so a configured column can never take this name.
EXTRA_FIELD_KEY = "_extra_field"


def literal(text: str) -> str:
    """Render text as a SQL string literal. The only place where quotes are escaped.

    Snowflake treats a backslash inside a literal as an escape character, so doubling the quotes
    is only safe for text without backslashes. The configuration models already reject them and
    this check keeps the guarantee local.
    """
    if "\\" in text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"text cannot be rendered as a SQL literal: {text!r}")
    return "'" + text.replace("'", "''") + "'"


_templates = Environment(
    loader=PackageLoader("partner_factory", "templates"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_templates.filters["literal"] = literal

PSEUDONYMIZATION_KEY_TABLE = "PSEUDONYMIZATION_KEY"


@dataclass(frozen=True)
class SourceObjects:
    """Names and SQL fragments of the loader objects of one source."""

    name: str
    description: str
    table: str
    pipe: str
    file_format: str
    file_format_options: list[str]
    record_expression: str
    file_pattern: str


def _file_format_options(source: Source) -> list[str]:
    if source.csv is None:
        return ["TYPE = JSON"]
    delimiter = "'\\t'" if source.csv.delimiter == "\t" else literal(source.csv.delimiter)
    quote = "NONE" if source.csv.quote == "none" else literal(source.csv.quote)
    return [
        "TYPE = CSV",
        f"FIELD_DELIMITER = {delimiter}",
        f"FIELD_OPTIONALLY_ENCLOSED_BY = {quote}",
        f"ENCODING = {literal(source.csv.encoding)}",
        # Raw keeps what the partner sent: no header skipping, no trimming, no NULL markers.
        "SKIP_HEADER = 0",
        "TRIM_SPACE = FALSE",
        "NULL_IF = ()",
        # A line with fewer or more fields than expected is loaded and judged by the processor.
        "ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE",
    ]


def _record_expression(source: Source) -> str:
    """The expression that builds the raw record from one staged row.

    For CSV, the position after the last configured column is kept under EXTRA_FIELD_KEY. It is
    null for a well-formed line, and lets the processor reject lines with more fields than
    expected instead of silently reading shifted or truncated values.
    """
    if source.csv is None:
        return "$1"
    keys = [*source.csv.columns, EXTRA_FIELD_KEY]
    pairs = ", ".join(f"{literal(key)}, ${position}" for position, key in enumerate(keys, start=1))
    return f"OBJECT_CONSTRUCT_KEEP_NULL({pairs})"


def source_objects(name: str, source: Source) -> SourceObjects:
    table = name.upper()
    return SourceObjects(
        name=name,
        description=source.description,
        table=table,
        pipe=f"{table}_PIPE",
        file_format=f"{table}_FORMAT",
        file_format_options=_file_format_options(source),
        record_expression=_record_expression(source),
        file_pattern=source.file_pattern,
    )


def normalize_public_key(pem: str) -> str:
    """Body of a PEM public key, the form ``ALTER USER ... SET RSA_PUBLIC_KEY`` expects."""
    # The key is sent to Snowflake and echoed in the output: never accept a private key here.
    if "-----BEGIN PUBLIC KEY-----" not in pem:
        raise ValueError("expected a PEM public key (-----BEGIN PUBLIC KEY-----)")
    body = "".join(line.strip() for line in pem.splitlines() if "-----" not in line)
    if not re.fullmatch(r"[A-Za-z0-9+/]{300,}={0,2}", body):
        raise ValueError("the file does not look like a PEM-encoded RSA public key")
    return body


def platform_sql(platform: Platform, service_public_key_pem: str) -> str:
    """Bootstrap of an environment: service identity, warehouse and storage integration."""
    return _templates.get_template("platform.sql.j2").render(
        platform=platform,
        sf=platform.snowflake,
        aws=platform.aws,
        service_public_key=normalize_public_key(service_public_key_pem),
    )


def partner_sql(platform: Platform, partner: Partner) -> str:
    """Database, schemas, stage and one raw table and pipe per source of a partner."""
    return _templates.get_template("partner.sql.j2").render(
        platform=platform,
        sf=platform.snowflake,
        partner=partner,
        database=platform.database(partner),
        landing_url=platform.landing_url(partner),
        key_table=PSEUDONYMIZATION_KEY_TABLE,
        sources=[source_objects(name, source) for name, source in partner.sources.items()],
    )
