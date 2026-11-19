"""Snowflake sessions for the factory.

Authentication is key-pair only. The account comes from the platform file; the private key is
read from the path in ``SNOWFLAKE_PRIVATE_KEY_FILE`` and never from the repository.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from contextlib import contextmanager

import snowflake.connector
from snowflake.connector import DictCursor, SnowflakeConnection
from snowflake.connector.errors import ProgrammingError
from snowflake.connector.util_text import split_statements

from partner_factory.config import Platform


class MissingCredentials(RuntimeError):
    pass


class StatementFailed(RuntimeError):
    """A statement of a script was rejected by Snowflake."""

    def __init__(self, statement: str, reason: str) -> None:
        super().__init__(f"{reason}\n  while running: {statement}")


@contextmanager
def connect(platform: Platform, *, administrator: bool = False) -> Iterator[SnowflakeConnection]:
    """Open a session as the service user, or as an administrator for the platform bootstrap.

    The administrator session takes user and role from ``SNOWFLAKE_USER`` and ``SNOWFLAKE_ROLE``
    and uses no warehouse, because the bootstrap is what creates it.
    """
    key_file = os.environ.get("SNOWFLAKE_PRIVATE_KEY_FILE")
    if not key_file:
        raise MissingCredentials("set SNOWFLAKE_PRIVATE_KEY_FILE to the path of the private key")

    if administrator:
        user, role = os.environ.get("SNOWFLAKE_USER"), os.environ.get("SNOWFLAKE_ROLE")
        if not user or not role:
            raise MissingCredentials(
                "the bootstrap runs as an administrator: set SNOWFLAKE_USER and SNOWFLAKE_ROLE"
            )
        warehouse = None
    else:
        user, role = platform.snowflake.service_user, platform.snowflake.pipeline_role
        warehouse = platform.snowflake.warehouse

    connection = snowflake.connector.connect(
        account=platform.snowflake.account,
        user=user,
        role=role,
        warehouse=warehouse,
        authenticator="SNOWFLAKE_JWT",
        private_key_file=key_file,
        private_key_file_pwd=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        application="partner_factory",
        # The administrator has its own defaults; the factory always works in UTC.
        session_parameters={"TIMEZONE": "UTC"},
        # Short-lived sessions that run nothing asynchronous: always log out on close.
        enable_server_session_keep_alive_auto_detection=False,
    )
    try:
        yield connection
    finally:
        connection.close()


def run_script(connection: SnowflakeConnection, sql: str) -> Iterator[tuple[str, str]]:
    """Run a script one statement at a time, yielding each with the status Snowflake returned.

    Statements run in order and stop at the first failure, so the caller always knows which
    statement failed and everything before it is already applied.
    """
    for statement, _ in split_statements(io.StringIO(sql), remove_comments=True):
        if not statement.strip():
            continue
        summary = " ".join(statement.split())
        with connection.cursor() as cursor:
            try:
                row = cursor.execute(statement).fetchone()
            except ProgrammingError as error:
                raise StatementFailed(summary, error.msg or str(error)) from error
        yield summary, str(row[0]) if row else ""


def query(connection: SnowflakeConnection, sql: str) -> list[dict]:
    """Run one statement and return its rows as dictionaries keyed by column name."""
    with connection.cursor(DictCursor) as cursor:
        return cursor.execute(sql).fetchall()
