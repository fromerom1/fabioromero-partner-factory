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
from datetime import datetime
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

from partner_factory.standard_model import (
    ENTITIES,
    SALE_STATUSES,
    TRANSFORM_KINDS,
    StandardField,
    Transform,
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


# A key, or a dotted path of keys, inside a raw record: `store_token`, `order.created_at`.
FieldPath = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,99}(\.[A-Za-z_][A-Za-z0-9_]{0,99}){0,9}$"),
]
# A Snowflake date and time format, such as `DD/MM/YYYY HH24:MI:SS`.
TimestampFormat = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9 :./"-]{1,60}$')]
# A literal value: a constant for a field, or one side of a value map.
ConstantText = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_ -]{0,49}$")]

BATCH_DATE_FORMATS = {"YYYYMMDD": "%Y%m%d", "YYYYMM": "%Y%m"}


def _timezone_exists(timezone: str) -> str:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"unknown IANA time zone '{timezone}'") from error
    return timezone


TimeZone = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9_+/-]{1,64}$"),
    AfterValidator(_timezone_exists),
]
# A store identifier as it appears in the standard model, after the mapping's transforms.
StoreId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")]


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


class BatchDate(StrictModel):
    """How the partner's batch date is read from the path of a file."""

    regex: SqlLiteralSafe
    """Regular expression with one group that captures the date: `sales_([0-9]{8})`."""
    format: Literal["YYYYMMDD", "YYYYMM"]

    def extract(self, path: str) -> str | None:
        """The captured date text of a path, or None when it is absent or not a date."""
        match = re.search(self.regex, path)
        if not match:
            return None
        try:
            datetime.strptime(match.group(1), BATCH_DATE_FORMATS[self.format])
        except ValueError:
            return None
        return match.group(1)

    @model_validator(mode="after")
    def _has_one_group(self) -> BatchDate:
        try:
            groups = re.compile(self.regex).groups
        except re.error as error:
            raise ValueError(f"regex is not a valid regular expression: {error}") from error
        if groups != 1:
            raise ValueError("regex must have exactly one capturing group, around the date")
        return self


class Source(StrictModel):
    """One kind of file a partner delivers. It becomes one raw table and one pipe."""

    description: FreeText
    format: Literal["csv", "json"]
    """``json`` covers both line-delimited files and a single document per file."""
    file_pattern: SqlLiteralSafe
    """Regular expression for the file path, relative to the partner's landing prefix."""
    example_file: SqlLiteralSafe
    """A path the pattern must match. Used to check that no two sources claim the same file."""
    batch_date: BatchDate
    csv: CsvOptions | None = None
    personal_data: bool = False
    """True when the records carry data about people, such as customer identifiers or contact
    details. The raw record is then tagged as personal data."""

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


class MoneyFormat(StrictModel):
    """How the partner writes an amount. A value that does not fit exactly is invalid.

    Declared rather than guessed: `63,98` is sixty-three euros in one notation and six thousand
    in another, and stripping separators would silently pick one.
    """

    unit: Literal["units", "cents"] = "units"
    """`cents` is an integer number of minor units, with no symbol or separators."""
    symbol: Literal["$", "R$", "US$", "€", "£"] | None = None
    """Optional currency symbol in front of the number."""
    thousands: Literal[",", ".", " "] | None = None
    """Optional grouping separator. When present, groups must be of exactly three digits."""
    decimal: Literal[".", ","] = "."
    """Decimal separator. At most two decimals are accepted, never rounded."""

    @model_validator(mode="after")
    def _is_consistent(self) -> MoneyFormat:
        if self.thousands == self.decimal:
            raise ValueError("thousands and decimal separators must differ")
        if self.unit == "cents" and (self.symbol or self.thousands or self.decimal != "."):
            raise ValueError("cents are plain integers: no symbol or separators")
        return self


class FieldMapping(StrictModel):
    """Where one field of the standard model comes from and how the partner writes it."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: FieldPath | None = Field(default=None, alias="from")
    """Key or dotted path inside the raw record."""
    constant: ConstantText | None = None
    """A fixed value, for partners that do not send the field."""
    transforms: tuple[Transform, ...] = ()
    """Applied in order, from the catalog in standard_model.TRANSFORM_KINDS."""
    format: TimestampFormat | None = None
    """Snowflake format of a timestamp field."""
    money: MoneyFormat | None = None
    """Notation of an amount field."""
    matches: SqlLiteralSafe | None = None
    """Regular expression a text value must match completely to be valid."""
    value_map: dict[ConstantText, ConstantText] | None = None
    """Partner status to standard status. Values missing from the map are invalid."""

    @model_validator(mode="after")
    def _one_origin(self) -> FieldMapping:
        if (self.source is None) == (self.constant is None):
            raise ValueError("set exactly one of 'from' and 'constant'")
        if self.constant is not None and (
            self.transforms or self.format or self.money or self.matches or self.value_map
        ):
            raise ValueError("a constant takes no transforms, format, money, matches or value_map")
        return self


class EntityMapping(StrictModel):
    """How one source of the partner fills one entity of the standard model."""

    source: FieldName
    explode: FieldPath | None = None
    """Array inside each record that holds the rows, for sources that send one document."""
    fields: dict[FieldName, FieldMapping]


class SalesTransactionMapping(EntityMapping):
    key: tuple[Literal["store_id", "transaction_id"], ...] = ("transaction_id",)
    """What makes a transaction unique for this partner. Duplicates collapse on it."""

    @field_validator("key")
    @classmethod
    def _key_is_valid(cls, key: tuple[str, ...]) -> tuple[str, ...]:
        if "transaction_id" not in key or len(set(key)) != len(key):
            raise ValueError("key must include transaction_id and not repeat a field")
        return key


class StandardModelMapping(StrictModel):
    store: EntityMapping
    sales_transaction: SalesTransactionMapping


def _field_problem(field: StandardField, mapping: FieldMapping) -> str | None:
    """What is wrong with the mapping of a standard field, given the kind of field it is."""
    for transform in mapping.transforms:
        if field.kind not in TRANSFORM_KINDS[transform]:
            return f"transform '{transform}' does not apply to a {field.kind} field"
    if field.kind == "timestamp" and not mapping.format:
        return "a timestamp field needs a 'format', and cannot be a constant"
    if field.kind != "timestamp" and mapping.format:
        return "'format' only applies to timestamp fields"
    if field.kind == "amount" and not mapping.money:
        return "an amount field needs 'money', the notation the partner writes amounts in"
    if field.kind != "amount" and mapping.money:
        return "'money' only applies to amount fields"
    if field.kind != "text" and mapping.matches:
        return "'matches' only applies to text fields"
    if field.kind != "status" and mapping.value_map:
        return "'value_map' only applies to the status field"
    if field.kind == "status":
        targets = [mapping.constant] if mapping.constant else (mapping.value_map or {}).values()
        unknown = sorted(set(targets) - set(SALE_STATUSES))
        if unknown:
            return f"unknown status {unknown}; the standard statuses are {list(SALE_STATUSES)}"
    if field.kind == "pseudonym" and (mapping.transforms or ("",))[-1] != "pseudonymize":
        # Customer identifiers never leave RAW in clear: the last step is always the hash.
        return "customer data must end with the 'pseudonymize' transform"
    return None


class Partner(StrictModel):
    partner_code: SnowflakeIdentifier
    display_name: FreeText
    country_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    """ISO 3166-1 alpha-2."""
    currency_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    """ISO 4217. Default currency of the partner's amounts."""
    timezone: TimeZone
    """IANA zone of the partner's local timestamps."""
    store_timezones: dict[StoreId, TimeZone] = {}
    """Stores whose local time is not the partner's, by standard store_id."""
    sources: Annotated[dict[FieldName, Source], Field(min_length=1)]
    standard_model: StandardModelMapping

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

    @model_validator(mode="after")
    def _batch_dates_can_be_read(self) -> Partner:
        """The batch date is read from the full path in the bucket, prefix included."""
        for name, source in self.sources.items():
            expected = source.batch_date.extract(source.example_file)
            if expected is None:
                raise ValueError(
                    f"sources.{name}.batch_date: the regex finds no "
                    f"{source.batch_date.format} date in '{source.example_file}'"
                )
            full_path = f"{self.prefix}/landing/{source.example_file}"
            if source.batch_date.extract(full_path) != expected:
                raise ValueError(
                    f"sources.{name}.batch_date: the regex matches the partner prefix in "
                    f"'{full_path}' before the file name; make it more specific"
                )
        return self

    @model_validator(mode="after")
    def _mappings_fit_the_standard_model(self) -> Partner:
        for entity_name, entity in ENTITIES.items():
            mapping: EntityMapping = getattr(self.standard_model, entity_name)
            where = f"standard_model.{entity_name}"
            source = self.sources.get(mapping.source)
            if source is None:
                raise ValueError(f"{where}.source: '{mapping.source}' is not one of the sources")
            if mapping.explode and source.csv:
                raise ValueError(f"{where}.explode only applies to JSON sources")

            for name, field_mapping in mapping.fields.items():
                field = entity.field(name)
                if field is None:
                    known = ", ".join(f.name for f in entity.fields)
                    raise ValueError(f"{where}.fields.{name}: not a standard field ({known})")
                origin = field_mapping.source
                if source.csv and origin and origin not in source.csv.columns:
                    raise ValueError(
                        f"{where}.fields.{name}: '{origin}' is not a column of '{mapping.source}'"
                    )
                problem = _field_problem(field, field_mapping)
                if problem:
                    raise ValueError(f"{where}.fields.{name}: {problem}")

            personal = [
                name
                for name in mapping.fields
                if entity.field(name).kind == "pseudonym" and not source.personal_data
            ]
            if personal:
                raise ValueError(
                    f"{where}.fields.{personal[0]}: it is read from '{mapping.source}', so that"
                    f" source must declare 'personal_data: true'"
                )

            missing = [f.name for f in entity.fields if f.required and f.name not in mapping.fields]
            if missing:
                raise ValueError(
                    f"{where}.fields: required fields not mapped: {', '.join(missing)}"
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
    analyst_role: SnowflakeIdentifier
    """Reads the standard model and the reports; customer references are masked."""
    fraud_credit_role: SnowflakeIdentifier
    """Reads the same as the analyst and also sees customer references."""

    @property
    def reader_roles(self) -> list[str]:
        return [self.analyst_role, self.fraud_credit_role]


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

    def historical_url(self, partner: Partner) -> str:
        return f"s3://{self.aws.bucket}/{partner.prefix}/historical/"


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
