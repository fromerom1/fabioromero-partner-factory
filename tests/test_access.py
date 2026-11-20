"""Who reads what, and that the check tells a correct database from a leaking one."""

from contextlib import nullcontext
from pathlib import Path

import pytest
from snowflake.connector.errors import ProgrammingError

from partner_factory import access
from partner_factory.config import load_partner, load_platform

REPO = Path(__file__).resolve().parents[1]
PLATFORM = load_platform(REPO / "platform" / "dev.yml")
PARTNERS = {path.stem: load_partner(path) for path in sorted((REPO / "partners").glob("*.yml"))}
SF = PLATFORM.snowflake
REFERENCES = (49, 48)  # rows of the sales table, rows with a customer reference


@pytest.mark.parametrize("name", PARTNERS)
def test_readers_get_the_standard_tables_and_the_reports_only(name: str) -> None:
    for_readers = {name for name, allowed in access.objects(PARTNERS[name]).items() if allowed}
    assert for_readers == {
        "PROCESSED.STD_STORE",
        "PROCESSED.STD_SALES_TRANSACTION",
        "ANALYTICS.RPT_BATCH_PROCESSING",
        "ANALYTICS.RPT_DAILY_SALES",
        "ANALYTICS.RPT_TOP_STORES",
    }
    pipeline_only = access.objects(PARTNERS[name]).keys() - for_readers
    assert {n for n in pipeline_only if n.startswith("RAW.")} == {
        f"RAW.{source.upper()}" for source in PARTNERS[name].sources
    } | {"RAW.PSEUDONYMIZATION_KEY"}
    # The checked views compute customer references from RAW: no table policy covers them.
    assert {"PROCESSED.CHECKED_SALES_TRANSACTION", "PROCESSED.REJECTED_RECORD"} <= pipeline_only


def test_personal_data_is_tagged_where_it_lives() -> None:
    assert access.tagged_columns(PARTNERS["tiendaviva_es"]) == {
        ("RAW.ORDERS", "RECORD"): "CUSTOMER_DATA",
        ("RAW.PSEUDONYMIZATION_KEY", "KEY_VALUE"): "PSEUDONYMIZATION_KEY",
        ("PROCESSED.STD_SALES_TRANSACTION", "CUSTOMER_REF"): "PSEUDONYM",
    }
    # No source of this partner carries customer data; the key and the column are tagged anyway.
    assert ("RAW.SALES", "RECORD") not in access.tagged_columns(PARTNERS["mercadolocal_br"])
    assert len(access.tagged_columns(PARTNERS["mercadolocal_br"])) == 2


def run_check(monkeypatch: pytest.MonkeyPatch, *, leaks=(), unmasked=(), protected=True):
    """Run the check against a fake database that follows the rules, except where told."""
    partner = PARTNERS["tiendaviva_es"]
    statements = []

    def query(role: str, sql: str) -> list[dict]:
        statements.append(sql)
        if sql.startswith("USE"):
            return []
        if "CUSTOMER_REF" in sql:
            sees = role != SF.analyst_role or role in unmasked
            return [{"TOTAL": REFERENCES[0], "VISIBLE": REFERENCES[1] if sees else 0}]
        name = sql.split("TIENDAVIVA_ES_DEV.")[1]
        if role != SF.pipeline_role and not access.objects(partner)[name] and name not in leaks:
            raise ProgrammingError("not authorized")
        return [{"COUNT(*)": 1}]

    monkeypatch.setattr(access, "query", query)
    monkeypatch.setattr(access, "_protections", lambda *arguments: {"policy": protected})
    report = access.check(nullcontext, PLATFORM, partner)
    # Every session drops its secondary roles before it reads anything.
    assert statements.count("USE SECONDARY ROLES NONE") == 3
    assert statements[0] == "USE SECONDARY ROLES NONE"
    return report


def test_a_database_that_follows_the_rules_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    report = run_check(monkeypatch)
    assert report.problems == []
    assert report.customer_refs == {
        SF.pipeline_role: REFERENCES,
        SF.analyst_role: (REFERENCES[0], 0),
        SF.fraud_credit_role: REFERENCES,
    }


def test_the_check_reports_every_kind_of_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_check(monkeypatch, leaks=["RAW.ORDERS"]).problems == [
        f"{SF.analyst_role} on RAW.ORDERS: read",
        f"{SF.fraud_credit_role} on RAW.ORDERS: read",
    ]
    assert run_check(monkeypatch, unmasked=[SF.analyst_role]).problems == [
        f"{SF.analyst_role} sees customer references"
    ]
    assert run_check(monkeypatch, protected=False).problems == ["missing: policy"]
