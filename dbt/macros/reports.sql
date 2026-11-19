{#- The date a report snapshot belongs to: today in the partner's time zone. Pass
    `--vars '{snapshot_date: 2026-10-08}'` through `factory dbt` to rebuild a past snapshot. -#}
{% macro snapshot_date() -%}
    {%- if var('snapshot_date', none) -%}
        '{{ var("snapshot_date") }}'::date
    {%- else -%}
        convert_timezone('{{ var("timezone") }}', current_timestamp())::date
    {%- endif -%}
{%- endmacro %}


{#- What the reports call sales: completed transactions, by the date of the sale in the
    partner's local time. Refunded, pending and cancelled transactions are not sales. -#}
{% macro completed_sales() %}
    select
        transaction_date_local as transaction_date,
        store_id,
        amount
    from {{ ref('std_sales_transaction') }}
    where status = 'COMPLETED'
{% endmacro %}


{#- Incremental strategy of the reports: the run replaces the snapshot of its date.

    dbt's delete+insert only deletes the keys present in the new result, so a run that
    produces no rows would leave the earlier snapshot of the day in place. This one deletes
    by the snapshot date of the run, whatever the result holds, in the same transaction as
    the insert. -#}
{% macro get_incremental_replace_snapshot_sql(arg_dict) %}
    {%- set target = arg_dict["target_relation"] -%}
    {%- set columns = get_quoted_csv(arg_dict["dest_columns"] | map(attribute="name")) -%}
    {%- set dml -%}
        delete from {{ target }} where snapshot_date = {{ snapshot_date() }};
        insert into {{ target }} ({{ columns }})
        select {{ columns }} from {{ arg_dict["temp_relation"] }}
    {%- endset -%}
    {% do return(snowflake_dml_explicit_transaction(dml)) %}
{% endmacro %}
