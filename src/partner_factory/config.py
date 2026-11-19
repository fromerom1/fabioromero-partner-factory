"""Configuration models.

Two kinds of file drive the factory:

* ``partners/<partner>.yml``: one per partner, the only file an onboarding touches.
* ``platform/<environment>.yml``: one per environment, shared with Terraform.

Every name that ends up inside generated SQL is constrained here, so the SQL templates can
interpolate configuration values without quoting or escaping them.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SnowflakeIdentifier = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")]
FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,99}$")]
# Regular expressions and file names are embedded in single-quoted SQL literals.
SqlLiteralSafe = Annotated[str, StringConstraints(min_length=1, pattern=r"^[^'\\\s;]+$")]


def _single_line(text: str) -> str:
    for char in text:
        category = unicodedata.category(char)
        if category.startswith("C") or category in ("Zl", "Zp"):
            raise ValueError("must be a single line without control characters")
    if "\\" in text:
        raise ValueError("must not contain a backslash")
    return text


# Human-readable text. It reaches Snowflake only inside string literals (object comments), so it
# must not be able to end a line or escape a quote. Quotes are allowed and escaped in sql.literal.
FreeText = Annotated[
    str, StringConstraints(min_length=1, max_length=200), AfterValidator(_single_line)
]


class StrictModel(BaseModel):
    """Unknown keys are errors: a typo in a YAML must fail validation, not be ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CsvOptions(StrictModel):
    """How a CSV source is split into fields.

    A header line is neither required nor skipped: every line is loaded and the processor drops
    the ones that repeat the column names, because partners send files with and without header.
    """

    columns: Annotated[list[FieldName], Field(min_length=1)]
    """Column names in file order. They become the keys of the raw record."""
    delimiter: Annotated[str, StringConstraints(pattern=r"^[,;|\t]$")] = ","
    quote: Literal['"', "none"] = '"'
    encoding: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_-]+$")] = "UTF8"

    @field_validator("columns")
    @classmethod
    def _columns_are_unique(cls, columns: list[str]) -> list[str]:
        duplicated = sorted({c for c in columns if columns.count(c) > 1})
        if duplicated:
            raise ValueError(f"duplicated column names: {', '.join(duplicated)}")
        return columns


class Source(StrictModel):
    """One kind of file a partner delivers. It becomes one raw table and one pipe."""

    description: FreeText
    format: Literal["csv", "json"]
    """``json`` covers both line-delimited files and a single document per file."""
    file_pattern: SqlLiteralSafe
    """Regular expression for the file path, relative to the partner's landing prefix."""
    example_file: SqlLiteralSafe
    """A path the pattern must match. Used to check that no two sources claim the same file."""
    csv: CsvOptions | None = None

    @model_validator(mode="after")
    def _check(self) -> Source:
        if (self.format == "csv") != (self.csv is not None):
            raise ValueError("the 'csv' block is required for format csv and not allowed otherwise")
        try:
            pattern = re.compile(self.file_pattern)
        except re.error as error:
            raise ValueError(f"file_pattern is not a valid regular expression: {error}") from error
        if not pattern.fullmatch(self.example_file):
            raise ValueError(f"file_pattern does not match example_file '{self.example_file}'")
        return self


class Partner(StrictModel):
    partner_code: SnowflakeIdentifier
    display_name: FreeText
    country_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    """ISO 3166-1 alpha-2."""
    currency_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    """ISO 4217. Default currency of the partner's amounts."""
    timezone: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_+/-]{1,64}$")]
    """IANA zone of the partner's local timestamps."""
    sources: Annotated[dict[FieldName, Source], Field(min_length=1)]

    @field_validator("timezone")
    @classmethod
    def _timezone_exists(cls, timezone: str) -> str:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown IANA time zone '{timezone}'") from error
        return timezone

    @model_validator(mode="after")
    def _sources_do_not_overlap(self) -> Partner:
        """A file matched by two pipes would be loaded twice, into two tables."""
        for name, source in self.sources.items():
            for other_name, other in self.sources.items():
                if name != other_name and re.fullmatch(other.file_pattern, source.example_file):
                    raise ValueError(
                        f"file_pattern of source '{other_name}' also matches "
                        f"'{source.example_file}', the example file of source '{name}'"
                    )
        return self

    @property
    def prefix(self) -> str:
        """Top-level prefix of the partner inside the bucket."""
        return self.partner_code.lower()


class SnowflakePlatform(StrictModel):
    account: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$")]
    warehouse: SnowflakeIdentifier
    pipeline_role: SnowflakeIdentifier
    service_user: SnowflakeIdentifier
    storage_integration: SnowflakeIdentifier


class AwsPlatform(StrictModel):
    account_id: Annotated[str, StringConstraints(pattern=r"^[0-9]{12}$")]
    region: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$")]
    bucket: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")]
    snowflake_role_name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9+=,.@_-]{1,64}$")]

    @property
    def snowflake_role_arn(self) -> str:
        return f"arn:aws:iam::{self.account_id}:role/{self.snowflake_role_name}"


class Platform(StrictModel):
    environment: SnowflakeIdentifier
    snowflake: SnowflakePlatform
    aws: AwsPlatform

    def database(self, partner: Partner) -> str:
        """Each partner owns one database per environment, e.g. ``MERCADOLOCAL_BR_DEV``."""
        return f"{partner.partner_code}_{self.environment}"

    def landing_url(self, partner: Partner) -> str:
        return f"s3://{self.aws.bucket}/{partner.prefix}/landing/"


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_partner(path: Path) -> Partner:
    partner = Partner.model_validate(_read_yaml(path))
    if path.stem != partner.partner_code.lower():
        raise ValueError(
            f"{path.name}: the file must be named after its partner_code "
            f"({partner.partner_code.lower()}.yml)"
        )
    return partner


def load_platform(path: Path) -> Platform:
    return Platform.model_validate(_read_yaml(path))
