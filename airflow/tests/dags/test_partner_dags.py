"""The DAG file imports cleanly and yields one DAG per partner file.

Runs outside the project environment, with the Airflow version of the Astro runtime:

    uv run --no-project --with apache-airflow==3.4.0 --with pytest \\
        pytest --rootdir airflow airflow/tests
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
os.environ.setdefault("FACTORY_HOME", str(REPO))

from airflow.dag_processing.dagbag import DagBag  # noqa: E402

DAGS = DagBag(dag_folder=str(REPO / "airflow" / "dags"))


def test_the_dag_file_imports_without_errors() -> None:
    assert DAGS.import_errors == {}


def test_every_partner_file_gets_a_dag() -> None:
    expected = {f"partner_{path.stem}" for path in (REPO / "partners").glob("*.yml")}
    assert expected and set(DAGS.dag_ids) == expected


def test_a_partner_is_deployed_before_it_is_processed() -> None:
    for dag in DAGS.dags.values():
        order = [task.task_id for task in dag.topological_sort()]
        assert order == ["deploy", "process_and_archive"]
        assert dag.max_active_runs == 1 and not dag.catchup
