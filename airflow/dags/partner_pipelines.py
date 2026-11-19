"""One DAG per partner, generated from the partner files.

Adding ``partners/<partner>.yml`` is all it takes for a partner to be scheduled: no DAG is
written by hand. Each task is a ``factory`` command, the same one an engineer runs from a
terminal, so every step has a single implementation and can be reproduced outside Airflow.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

FACTORY_HOME = Path(os.environ.get("FACTORY_HOME", "/usr/local/airflow/partner-factory"))
# Runs the factory in its own environment, pinned by uv.lock.
FACTORY = "uv run --frozen --no-dev factory"

for partner_file in sorted((FACTORY_HOME / "partners").glob("*.yml")):
    # The name goes into a shell command: only take names that can be partner codes.
    if not re.fullmatch(r"[a-z][a-z0-9_]*", partner_file.stem):
        continue
    partner = partner_file.stem.upper()

    with DAG(
        dag_id=f"partner_{partner_file.stem}",
        description=f"Daily processing of {partner}: load checks, standard model, reports, archive",
        schedule="0 6 * * *",
        start_date=datetime(2026, 11, 1),
        catchup=False,
        # A run reads what the previous one left: never two at a time for the same partner.
        max_active_runs=1,
        default_args={
            "retries": 1,
            "retry_delay": timedelta(minutes=5),
            "cwd": str(FACTORY_HOME),
            "append_env": True,
        },
        tags=["partner-factory", partner],
    ):
        # Creates anything missing and stops the run if a deployed pipe or file format no
        # longer matches the configuration.
        deploy = BashOperator(task_id="deploy", bash_command=f"{FACTORY} deploy {partner}")

        # Builds the standard model and the reports with their tests and, only if they pass,
        # moves the processed files from landing to historical. Safe to retry.
        process_and_archive = BashOperator(
            task_id="process_and_archive", bash_command=f"{FACTORY} archive {partner}"
        )

        deploy >> process_and_archive
