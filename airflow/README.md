# Orchestration

Local Airflow on the Astro CLI. `dags/partner_pipelines.py` generates one DAG per file in
`partners/`, so onboarding a partner schedules it without writing a DAG.

Each DAG runs daily and has two tasks, both plain `factory` commands:

1. `deploy`: creates anything missing and stops the run if a deployed pipe or file format no
   longer matches the configuration.
2. `process_and_archive`: builds the standard model and the reports with their tests and, only
   if they pass, moves the processed files from landing to historical.

Airflow carries no pipeline logic and no credentials of its own. The repository is mounted into
the containers, and the factory runs in its own environment built from `uv.lock`, so dbt and
Airflow never share dependencies.

## Run it

```bash
cd airflow
FACTORY_SNOWFLAKE_KEY=/absolute/path/to/svc_dev_rsa_key.p8 astro dev start
```

The UI is at http://localhost:18280 (not Airflow's default 8080) and the metadata database on
port 15434; both are set in `.astro/config.yaml`. DAGs start paused: unpausing one makes the
scheduler create and run the latest daily run. The first task of the first run takes a minute
longer, while `uv` builds the factory environment inside the container.

```bash
astro dev run dags unpause partner_mercadolocal_br
astro dev run dags list-runs partner_mercadolocal_br
astro dev stop
```

## What CI covers

CI imports the DAG file with the same Airflow version and checks there is one DAG per partner
with the two tasks in order. It does not run them: a full run needs Snowflake. The full run was
done on this local Airflow with the scheduler active, for both partners.
