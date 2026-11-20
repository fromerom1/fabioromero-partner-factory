"""Who reads what in a partner database, and the check that proves it against Snowflake.

The rules, the same for every partner:

- The pipeline reads everything and sees customer references.
- The analyst and fraud/credit roles read the standard tables and the reports, nothing else.
  RAW, the checked views (they compute customer references straight from RAW, so the masking
  policy of the table does not cover them), the rejected records and the pseudonymization
  key stay with the pipeline.
- Only fraud/credit, besides the pipeline, sees customer references. The analyst gets NULL.

The check opens one session per role with secondary roles off, so that nothing the user holds
besides that role can lend it access. It reports counts only, never a value.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import ProgrammingError

from partner_factory.config import Partner, Platform
from partner_factory.connection import query
from partner_factory.sql import (
    CUSTOMER_REF_POLICY,
    PII_CUSTOMER_DATA,
    PII_KEY,
    PII_PSEUDONYM,
    PII_TAG,
    PSEUDONYMIZATION_KEY_TABLE,
    literal,
)

SALES = "PROCESSED.STD_SALES_TRANSACTION"
READ, DENIED = "read", "denied"

Session = Callable[[str], AbstractContextManager[SnowflakeConnection]]


def objects(partner: Partner) -> dict[str, bool]:
    """Every table and view of a partner database, and whether readers may read it."""
    pipeline_only = [f"RAW.{source.upper()}" for source in partner.sources]
    pipeline_only += [
        f"RAW.{PSEUDONYMIZATION_KEY_TABLE}",
        "PROCESSED.CHECKED_STORE",
        "PROCESSED.CHECKED_SALES_TRANSACTION",
        "PROCESSED.REJECTED_RECORD",
    ]
    for_readers = [
        "PROCESSED.STD_STORE",
        SALES,
        "ANALYTICS.RPT_BATCH_PROCESSING",
        "ANALYTICS.RPT_DAILY_SALES",
        "ANALYTICS.RPT_TOP_STORES",
    ]
    return dict.fromkeys(pipeline_only, False) | dict.fromkeys(for_readers, True)


def tagged_columns(partner: Partner) -> dict[tuple[str, str], str]:
    """The columns that must carry the personal-data tag, and with which value."""
    tagged = {
        (f"RAW.{name.upper()}", "RECORD"): PII_CUSTOMER_DATA
        for name, source in partner.sources.items()
        if source.personal_data
    }
    # The key is tagged as a table; its columns carry the tag by inheritance.
    tagged[(f"RAW.{PSEUDONYMIZATION_KEY_TABLE}", "KEY_VALUE")] = PII_KEY
    tagged[(SALES, "CUSTOMER_REF")] = PII_PSEUDONYM
    return tagged


@dataclass
class Report:
    roles: list[str]
    access: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per object, what each role got: ``read`` or ``denied``."""
    customer_refs: dict[str, tuple[int, int] | None] = field(default_factory=dict)
    """Per role, rows of the sales table and how many show a customer reference."""
    protections: dict[str, bool] = field(default_factory=dict)
    """Each tag and policy that must be in place, and whether it is."""
    problems: list[str] = field(default_factory=list)


def check(session: Session, platform: Platform, partner: Partner) -> Report:
    """Compare what each role can read and see with the rules above."""
    database = platform.database(partner)
    sf = platform.snowflake
    report = Report(roles=[sf.pipeline_role, sf.analyst_role, sf.fraud_credit_role])
    expected = objects(partner)

    for role in report.roles:
        with session(role) as connection:
            query(connection, "USE SECONDARY ROLES NONE")
            for name in expected:
                try:
                    query(connection, f"SELECT COUNT(*) FROM {database}.{name}")
                    got = READ
                except ProgrammingError:
                    got = DENIED
                report.access.setdefault(name, {})[role] = got
                allowed = expected[name] or role == sf.pipeline_role
                if (got == READ) != allowed:
                    report.problems.append(f"{role} on {name}: {got}")
            report.customer_refs[role] = None
            if report.access[SALES][role] == READ:
                counts = query(
                    connection,
                    f"SELECT COUNT(*) AS TOTAL, COUNT(CUSTOMER_REF) AS VISIBLE"
                    f" FROM {database}.{SALES}",
                )[0]
                report.customer_refs[role] = (counts["TOTAL"], counts["VISIBLE"])
            if role == sf.pipeline_role:
                report.protections = _protections(connection, database, partner)

    real = report.customer_refs[sf.pipeline_role]
    if real is not None:
        if report.customer_refs[sf.analyst_role] not in (None, (real[0], 0)):
            report.problems.append(f"{sf.analyst_role} sees customer references")
        if report.customer_refs[sf.fraud_credit_role] != real:
            report.problems.append(f"{sf.fraud_credit_role} does not see customer references")
    missing = [name for name, found in report.protections.items() if not found]
    report.problems += [f"missing: {name}" for name in missing]
    return report


def _protections(connection: SnowflakeConnection, database: str, partner: Partner) -> dict:
    found = {}
    for (table, column), value in tagged_columns(partner).items():
        tags = query(
            connection,
            f"SELECT TAG_DATABASE, TAG_SCHEMA, TAG_NAME, TAG_VALUE"
            f" FROM TABLE({database}.INFORMATION_SCHEMA.TAG_REFERENCES_ALL_COLUMNS("
            f"{literal(f'{database}.{table}')}, 'table')) WHERE COLUMN_NAME = {literal(column)}",
        )
        found[f"tag {PII_TAG} = {value} on {table}.{column}"] = any(
            (row["TAG_DATABASE"], row["TAG_SCHEMA"], row["TAG_NAME"], row["TAG_VALUE"])
            == (database, "RAW", PII_TAG, value)
            for row in tags
        )
    policies = query(
        connection,
        f"SELECT POLICY_DB, POLICY_SCHEMA, POLICY_NAME, REF_COLUMN_NAME"
        f" FROM TABLE({database}.INFORMATION_SCHEMA.POLICY_REFERENCES("
        f"REF_ENTITY_NAME => {literal(f'{database}.{SALES}')}, REF_ENTITY_DOMAIN => 'table'))",
    )
    found[f"masking policy {CUSTOMER_REF_POLICY} on {SALES}.CUSTOMER_REF"] = any(
        (row["POLICY_DB"], row["POLICY_SCHEMA"], row["POLICY_NAME"], row["REF_COLUMN_NAME"])
        == (database, "PROCESSED", CUSTOMER_REF_POLICY, "CUSTOMER_REF")
        for row in policies
    )
    return found
