"""Command line of the partner factory.

Run from the repository root::

    factory validate
    factory platform bootstrap --service-public-key <file>   # once per environment, as admin
    factory deploy MERCADOLOCAL_BR
    factory platform aws-vars                                # values Terraform needs from Snowflake
    factory load-status MERCADOLOCAL_BR
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from partner_factory import sql
from partner_factory.config import Partner, Platform, load_partner, load_platform
from partner_factory.connection import (
    MissingCredentials,
    StatementFailed,
    connect,
    query,
    run_script,
)

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="Config-driven partner pipelines."
)
platform_app = typer.Typer(
    no_args_is_help=True, help="Per-environment setup shared by all partners."
)
app.add_typer(platform_app, name="platform")

PARTNERS_DIR = Path("partners")
PLATFORM_DIR = Path("platform")

EnvOption = Annotated[
    str,
    typer.Option("--env", envvar="FACTORY_ENV", help="Environment: selects platform/<env>.yml."),
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Print the SQL instead of running it.")
]


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


def _platform(env: str) -> Platform:
    path = PLATFORM_DIR / f"{env.lower()}.yml"
    if not path.exists():
        raise _fail(f"{path} not found. Run the factory from the repository root.")
    try:
        return load_platform(path)
    except ValidationError as error:
        raise _fail(f"{path}: {error}") from error


def _partner(partner_code: str) -> Partner:
    path = PARTNERS_DIR / f"{partner_code.lower()}.yml"
    if not path.exists():
        raise _fail(f"{path} not found. Known partners: {_known_partners()}")
    try:
        return load_partner(path)
    except (ValidationError, ValueError) as error:
        raise _fail(f"{path}: {error}") from error


def _known_partners() -> str:
    return ", ".join(sorted(p.stem.upper() for p in PARTNERS_DIR.glob("*.yml"))) or "none"


def _run(platform: Platform, script: str, *, administrator: bool = False) -> None:
    try:
        with connect(platform, administrator=administrator) as connection:
            identity = query(connection, "SELECT CURRENT_USER() AS U, CURRENT_ROLE() AS R")[0]
            account = platform.snowflake.account
            typer.echo(f"Connected to {account} as {identity['U']} ({identity['R']})")
            for statement, status in run_script(connection, script):
                typer.echo(f"  {statement[:70]:<70}  {status}")
    except (MissingCredentials, StatementFailed) as error:
        raise _fail(str(error)) from error


def _print_table(rows: list[dict]) -> None:
    if not rows:
        typer.echo("  (no rows)")
        return
    columns = list(rows[0])
    widths = {c: max(len(c), *(len(str(row[c])) for row in rows)) for c in columns}
    typer.echo("  ".join(f"{c:<{widths[c]}}" for c in columns))
    for row in rows:
        typer.echo("  ".join(f"{row[c]!s:<{widths[c]}}" for c in columns))


@app.command()
def validate(env: EnvOption = "dev") -> None:
    """Validate the platform file and every partner configuration. Needs no connection."""
    _platform(env)
    failures = 0
    for path in sorted(PARTNERS_DIR.glob("*.yml")):
        try:
            partner = load_partner(path)
            typer.echo(f"ok    {path}  {partner.partner_code}: {', '.join(partner.sources)}")
        except (ValidationError, ValueError) as error:
            failures += 1
            typer.secho(f"FAIL  {path}\n{error}", fg=typer.colors.RED, err=True)
    if failures:
        raise typer.Exit(code=1)


@app.command()
def deploy(partner_code: str, env: EnvOption = "dev", dry_run: DryRunOption = False) -> None:
    """Create the loader of a partner: database, schemas, stage, raw tables and pipes."""
    platform, partner = _platform(env), _partner(partner_code)
    script = sql.partner_sql(platform, partner)
    if dry_run:
        typer.echo(script)
        return
    _run(platform, script)


@app.command("load-status")
def load_status(partner_code: str, env: EnvOption = "dev") -> None:
    """Show the state of each pipe and the records loaded per file."""
    platform, partner = _platform(env), _partner(partner_code)
    database = platform.database(partner)
    sources = [sql.source_objects(name, source) for name, source in partner.sources.items()]
    per_file = "\nUNION ALL\n".join(
        f"SELECT '{s.name}' AS SOURCE, SOURCE_FILE, COUNT(*) AS RECORDS,"
        f" MAX(FILE_ROW_NUMBER) AS LAST_ROW,"
        f" CONVERT_TIMEZONE('UTC', MAX(LOADED_AT))::TIMESTAMP_NTZ(0) AS LOADED_AT_UTC"
        f" FROM {database}.RAW.{s.table} GROUP BY ALL"
        for s in sources
    )
    try:
        with connect(platform) as connection:
            pipes = []
            for s in sources:
                pipe = f"{database}.RAW.{s.pipe}"
                status = json.loads(
                    query(connection, f"SELECT SYSTEM$PIPE_STATUS('{pipe}') AS S")[0]["S"]
                )
                pipes.append(
                    {
                        "PIPE": pipe,
                        "STATE": status.get("executionState"),
                        "PENDING_FILES": status.get("pendingFileCount"),
                    }
                )
            _print_table(pipes)
            typer.echo("")
            _print_table(query(connection, f"{per_file}\nORDER BY SOURCE, SOURCE_FILE"))
    except MissingCredentials as error:
        raise _fail(str(error)) from error


@platform_app.command()
def bootstrap(
    service_public_key: Annotated[
        Path,
        typer.Option(exists=True, dir_okay=False, help="PEM public key of the service user."),
    ],
    env: EnvOption = "dev",
    dry_run: DryRunOption = False,
) -> None:
    """Create the service identity, warehouse and storage integration of an environment.

    The only command that needs an administrator: set SNOWFLAKE_USER, SNOWFLAKE_ROLE and
    SNOWFLAKE_PRIVATE_KEY_FILE to an account administrator for this one run.
    """
    platform = _platform(env)
    try:
        script = sql.platform_sql(platform, service_public_key.read_text())
    except ValueError as error:
        raise _fail(f"{service_public_key}: {error}") from error
    if dry_run:
        typer.echo(script)
        return
    _run(platform, script, administrator=True)


@platform_app.command("aws-vars")
def aws_vars(
    env: EnvOption = "dev",
    out: Annotated[Path, typer.Option(help="Terraform variables file to write.")] = Path(
        "infra/aws/snowflake.auto.tfvars.json"
    ),
) -> None:
    """Write the values AWS needs from Snowflake: who assumes the role and where events go.

    They only exist once the storage integration and the first pipe have been created, which is
    why Terraform is applied twice when an environment is set up.
    """
    platform = _platform(env)
    integration = platform.snowflake.storage_integration
    try:
        with connect(platform) as connection:
            properties = {
                row["property"]: row["property_value"]
                for row in query(connection, f"DESC INTEGRATION {integration}")
            }
            channels = {
                row["notification_channel"]
                for row in query(connection, "SHOW PIPES IN ACCOUNT")
                if row["notification_channel"]
                and row["database_name"].endswith(f"_{platform.environment}")
                and row["owner"] == platform.snowflake.pipeline_role
            }
    except MissingCredentials as error:
        raise _fail(str(error)) from error

    if len(channels) > 1:
        raise _fail(f"expected one notification channel for the bucket, found: {sorted(channels)}")
    variables = {
        "snowflake_iam_user_arn": properties["STORAGE_AWS_IAM_USER_ARN"],
        "snowflake_external_id": properties["STORAGE_AWS_EXTERNAL_ID"],
        "snowpipe_sqs_arn": channels.pop() if channels else None,
    }
    out.write_text(json.dumps(variables, indent=2) + "\n")
    typer.echo(f"Wrote {out}")
    if variables["snowpipe_sqs_arn"] is None:
        typer.secho(
            "No pipe exists yet: deploy a partner and run this command again to enable"
            " the bucket notification.",
            fg=typer.colors.YELLOW,
        )
