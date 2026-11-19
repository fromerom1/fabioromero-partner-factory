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
