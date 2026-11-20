"""A report run replaces the snapshot of its date, also when it comes out empty.

Runs dbt against the DEV database of a partner, so it is not part of the default test run:

    uv run pytest -m integration

It works on two snapshot dates in the past and removes them at the end; real snapshots are
never touched.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from partner_factory.cli import _split_vars
from partner_factory.config import load_partner, load_platform
from partner_factory.connection import connect, query

PARTNER = "MERCADOLOCAL_BR"
REPORTS = ("RPT_BATCH_PROCESSING", "RPT_DAILY_SALES", "RPT_TOP_STORES")
EARLIER, RERUN = "1999-01-01", "1999-01-02"
EMPTY = {"batch_report_dates": 0, "sales_report_dates": 0, "top_stores_per_date": 0}


def test_caller_vars_are_merged_into_the_partner_vars() -> None:
    arguments = ["run", "--vars", "{snapshot_date: 2026-10-08}", "--select", "x", "--vars={a: 1}"]
    rest, extra = _split_vars(arguments)
    assert rest == ["run", "--select", "x"]
    assert json.loads(json.dumps(extra, default=str)) == {"snapshot_date": "2026-10-08", "a": 1}


def run_reports(**variables: object) -> None:
    command = [str(Path(sys.executable).parent / "factory"), "dbt", PARTNER, "run"]
    command += ["--select", "partner_factory.analytics", "--vars", json.dumps(variables)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout[-2000:]


@pytest.mark.integration
def test_an_empty_run_replaces_the_snapshot_of_its_date_and_no_other() -> None:
    platform = load_platform(Path("platform/dev.yml"))
    database = platform.database(load_partner(Path(f"partners/{PARTNER.lower()}.yml")))

    def rows(connection, report: str) -> dict[str, int]:
        counted = query(
            connection,
            f"SELECT SNAPSHOT_DATE::VARCHAR AS D, COUNT(*) AS N FROM {database}.ANALYTICS.{report}"
            f" GROUP BY ALL",
        )
        return {row["D"]: row["N"] for row in counted}

    with connect(platform) as connection:
        try:
            before = {report: rows(connection, report) for report in REPORTS}
            run_reports(snapshot_date=EARLIER)
            run_reports(snapshot_date=RERUN)
            filled = {report: rows(connection, report) for report in REPORTS}
            run_reports(snapshot_date=RERUN, **EMPTY)
            for report in REPORTS:
                assert filled[report][EARLIER] > 0 and filled[report][RERUN] > 0, report
                # The empty run removed its own date and left every other snapshot as it was.
                assert rows(connection, report) == before[report] | {
                    EARLIER: filled[report][EARLIER]
                }, report
        finally:
            for report in REPORTS:
                query(
                    connection,
                    f"DELETE FROM {database}.ANALYTICS.{report}"
                    f" WHERE SNAPSHOT_DATE IN ('{EARLIER}', '{RERUN}')",
                )
