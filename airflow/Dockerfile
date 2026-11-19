FROM astrocrpublic.azurecr.io/runtime:3.4-1

# The repository is mounted here (docker-compose.override.yml): the DAGs read the partner files
# from it and the tasks run the same `factory` commands an engineer runs from a terminal.
ENV FACTORY_HOME=/usr/local/airflow/partner-factory \
    # The factory gets its own environment, built from uv.lock on first use, so its
    # dependencies (dbt among them) never mix with Airflow's.
    UV_PROJECT_ENVIRONMENT=/usr/local/airflow/factory-venv \
    SNOWFLAKE_PRIVATE_KEY_FILE=/usr/local/airflow/secrets/snowflake_key.p8
