"""Moves processed files from a partner's landing prefix to its historical prefix.

Everything happens through the partner's stages in Snowflake (LIST, COPY FILES, REMOVE), so
the pipeline needs no AWS credentials of its own.

A file is only archived when what sits in landing is exactly what was loaded, completely. The
order per file is copy, confirm the copy, then remove: a failure at any point leaves the file
in landing, and running again finishes the job without duplicating anything.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import Error as SnowflakeError

from partner_factory.config import Partner, Platform
from partner_factory.connection import query
from partner_factory.sql import literal

LOADED = "loaded"
NOT_LOADED = "not loaded"
LOAD_FAILED = "load failed"
PARTIALLY_LOADED = "partially loaded"
CHANGED_AFTER_LOAD = "changed after load"
LOAD_UNVERIFIED = "load unverified"
ARCHIVED = "archived"

# File names come from the partner. Only names made of these characters are used in commands;
# anything else stays in landing and is reported.
_SAFE_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./=-]*")


@dataclass(frozen=True)
class LandingFile:
    """A file as it sits in the landing prefix right now."""

    path: str
    """Relative to the landing prefix."""
    content_key: str
    """Checksum of the content, as the stage reports it."""


@dataclass(frozen=True)
class Load:
    """What the platform knows about the load of a file."""

    content_key: str | None
    """Checksum of the content that was loaded into RAW. None when no record was loaded."""
    records: int
    status: str | None
    """Status of the last load in the copy history. None once the history has expired."""
    parsed: int | None
    errors: int | None


def state(file: LandingFile, load: Load | None) -> str:
    """Where a landing file stands. Only LOADED files can be archived."""
    if load is None:
        return NOT_LOADED
    if load.content_key is None:
        return LOAD_FAILED
    if load.content_key != file.content_key:
        # Re-sent under the same name: Snowpipe does not load a file name twice, so what is in
        # landing now is not what is in RAW.
        return CHANGED_AFTER_LOAD
    if load.status is None:
        # The copy history is gone (it lasts 14 days), and with it the only count of what the
        # file held. Records in RAW with the right checksum do not prove that all of them
        # are there, so the file is not archived on that alone.
        return LOAD_UNVERIFIED
    if load.status != "Loaded" or load.errors or load.parsed != load.records:
        return PARTIALLY_LOADED
    return LOADED


def _relative(path: str, marker: str) -> str:
    return path.split(marker, 1)[1]


def _exact(path: str) -> str:
    """A regular expression that matches exactly one path. Stage patterns are matched against
    the whole path relative to the stage, so nothing else under it can match."""
    return path.replace(".", "[.]")


def _listing(connection: SnowflakeConnection, stage: str, marker: str) -> dict[str, str]:
    return {
        _relative(row["name"], marker): row["md5"] for row in query(connection, f"LIST @{stage}")
    }


def landing_files(
    connection: SnowflakeConnection, platform: Platform, partner: Partner
) -> list[tuple[LandingFile, str]]:
    """Every file in landing with its state, compared with RAW and the copy history."""
    database = platform.database(partner)
    loads: dict[str, Load] = {}
    for source in partner.sources:
        table = f"{database}.RAW.{source.upper()}"
        history = {
            row["FILE_NAME"]: row
            for row in query(
                connection,
                f"SELECT FILE_NAME, STATUS, ROW_PARSED, ERROR_COUNT"
                f" FROM TABLE({database}.INFORMATION_SCHEMA.COPY_HISTORY("
                f"TABLE_NAME => {literal(table)},"
                f" START_TIME => DATEADD(day, -13, CURRENT_TIMESTAMP())))"
                f" QUALIFY ROW_NUMBER() OVER (PARTITION BY FILE_NAME"
                f" ORDER BY LAST_LOAD_TIME DESC) = 1",
            )
        }
        loaded = {
            _relative(row["SOURCE_FILE"], "/landing/"): row
            for row in query(
                connection,
                f"SELECT SOURCE_FILE, COUNT(*) AS RECORDS,"
                # A file name holds one content; two would mean it was loaded twice.
                f" IFF(COUNT(DISTINCT FILE_CONTENT_KEY) = 1, ANY_VALUE(FILE_CONTENT_KEY), '')"
                f" AS CONTENT_KEY FROM {table} GROUP BY SOURCE_FILE",
            )
        }
        for path in history.keys() | loaded.keys():
            past, rows = history.get(path), loaded.get(path)
            loads[path] = Load(
                content_key=rows["CONTENT_KEY"] if rows else None,
                records=rows["RECORDS"] if rows else 0,
                status=past["STATUS"] if past else None,
                parsed=past["ROW_PARSED"] if past else None,
                errors=past["ERROR_COUNT"] if past else None,
            )

    files = [
        LandingFile(path, content_key)
        for path, content_key in sorted(
            _listing(connection, f"{database}.RAW.LANDING", "/landing/").items()
        )
    ]
    return [(file, state(file, loads.get(file.path))) for file in files]


def archive(
    connection: SnowflakeConnection,
    platform: Platform,
    partner: Partner,
    files: list[LandingFile],
) -> Iterator[tuple[str, str]]:
    """Move the given files to historical, yielding what happened to each.

    ``files`` is the list taken before the data was processed, so nothing that arrived during
    the run is touched. Each file is checked again against landing and historical right before
    it is moved.
    """
    database = platform.database(partner)
    landing, historical = f"{database}.RAW.LANDING", f"{database}.RAW.HISTORICAL"
    in_landing = _listing(connection, landing, "/landing/")
    in_historical = _listing(connection, historical, "/historical/")

    for file in files:
        try:
            outcome = _move(connection, landing, historical, file, in_landing, in_historical)
        except SnowflakeError as error:
            # One file that cannot be moved does not stop the others.
            outcome = f"left in landing: {' '.join(str(error).split())}"
        if outcome:
            yield file.path, outcome


def _move(
    connection: SnowflakeConnection,
    landing: str,
    historical: str,
    file: LandingFile,
    in_landing: dict[str, str],
    in_historical: dict[str, str],
) -> str | None:
    """Move one file and say what happened. None when an earlier attempt already moved it."""
    if not _SAFE_PATH.fullmatch(file.path):
        return "left in landing: unsupported characters in the file name"
    if in_landing.get(file.path) != file.content_key:
        return "left in landing: replaced during the run" if file.path in in_landing else None

    archived = in_historical.get(file.path)
    if archived is not None and archived != file.content_key:
        return "left in landing: historical holds a different file with this name"
    if archived is None:
        # COPY FILES keeps the path relative to the source stage, subfolders included.
        query(
            connection,
            f"COPY FILES INTO @{historical} FROM @{landing} FILES = ({literal(file.path)})",
        )
        copies = query(connection, f"LIST @{historical} PATTERN = {literal(_exact(file.path))}")
        if [row["md5"] for row in copies] != [file.content_key]:
            return "left in landing: the copy in historical could not be confirmed"

    # Remove exactly this file, and only while landing still holds the content just copied.
    target_files = query(connection, f"LIST @{landing} PATTERN = {literal(_exact(file.path))}")
    if [row["md5"] for row in target_files] != [file.content_key]:
        return "left in landing: replaced during the run"
    query(connection, f"REMOVE @{landing} PATTERN = {literal(_exact(file.path))}")
    return ARCHIVED
