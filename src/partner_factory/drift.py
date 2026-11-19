"""Compares what is deployed with what the configuration generates.

`factory deploy` only ever creates what is missing, so a change to a source that is already
deployed (a new CSV column, another delimiter, another file pattern) would be silently ignored.
This module finds those differences so that the deploy can stop and say what to do. It never
changes anything: replacing a pipe drops its load history, which is a decision for a person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from snowflake.connector import SnowflakeConnection

from partner_factory import sql
from partner_factory.config import Partner, Platform
from partner_factory.connection import query

PROCEDURE = """\
The configuration no longer matches what is deployed. Nothing was changed: `deploy` never
replaces a pipe or a file format, because replacing a pipe drops its load history.

To apply the change to {partner}:
  1. factory archive {partner}
       Process and move what is already loaded, so no file in landing can be loaded twice.
  2. ALTER PIPE <pipe> SET PIPE_EXECUTION_PAUSED = TRUE;
       Then wait until `factory load-status {partner}` shows no pending files.
  3. A file format:  ALTER FILE FORMAT <format> SET <option> = <value>;
                     ALTER PIPE <pipe> SET PIPE_EXECUTION_PAUSED = FALSE;
     A pipe:         DROP PIPE <pipe>;
  4. factory deploy {partner}
       Recreates a dropped pipe from the configuration and confirms nothing differs.
  5. Files delivered while a pipe did not exist are loaded with ALTER PIPE <pipe> REFRESH;
"""


@dataclass(frozen=True)
class Difference:
    kind: str
    name: str
    detail: str


def _normalized(statement: str) -> str:
    return " ".join(statement.split()).rstrip(";")


def compare_pipe(name: str, deployed: str, generated: str) -> Difference | None:
    """Pipes are compared by their COPY statement, ignoring only layout."""
    if _normalized(deployed) == _normalized(generated):
        return None
    return Difference(
        "pipe",
        name,
        f"deployed:  {_normalized(deployed)}\n    generated: {_normalized(generated)}",
    )


def compare_format(name: str, deployed: dict, generated: dict) -> Difference | None:
    """Only the options the factory sets are compared; the rest are Snowflake defaults."""
    changed = [
        f"{option}: deployed {deployed.get(option)!r}, generated {value!r}"
        for option, value in generated.items()
        if deployed.get(option) != value
    ]
    return Difference("file format", name, "; ".join(changed)) if changed else None


def find(connection: SnowflakeConnection, platform: Platform, partner: Partner) -> list[Difference]:
    """Differences between the deployed loader of a partner and its configuration."""
    database = platform.database(partner)
    if not query(connection, f"SHOW DATABASES LIKE {sql.literal(database)}"):
        return []  # first deploy

    pipes = {
        row["name"]: row["definition"]
        for row in query(connection, f"SHOW PIPES IN SCHEMA {database}.RAW")
    }
    formats = {
        row["name"]: json.loads(row["format_options"])
        for row in query(connection, f"SHOW FILE FORMATS IN SCHEMA {database}.RAW")
    }
    generated_pipes = sql.pipe_definitions(platform, partner)

    differences = []
    for source_name, source in partner.sources.items():
        objects = sql.source_objects(source_name, source)
        if objects.pipe in pipes:
            differences.append(
                compare_pipe(
                    f"{database}.RAW.{objects.pipe}",
                    pipes[objects.pipe],
                    generated_pipes[objects.pipe],
                )
            )
        if objects.file_format in formats:
            differences.append(
                compare_format(
                    f"{database}.RAW.{objects.file_format}",
                    formats[objects.file_format],
                    sql.file_format_options(source),
                )
            )
    return [difference for difference in differences if difference]
