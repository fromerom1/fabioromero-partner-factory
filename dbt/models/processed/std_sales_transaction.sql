{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=var('sales_transaction').key,
        on_schema_change='fail'
    )
}}

{#-
    One row per transaction: the current version of each key.

    Incremental and idempotent. Each run reads the valid records loaded since the newest one
    already processed, minus a lookback window, and puts them next to the rows the table
    already holds for the same keys. The current version is then chosen among all of them, so
    a record that arrives late but is older than the stored one never replaces it, and running
    twice gives the same table.
-#}

{%- set key = var('sales_transaction').key | join(', ') -%}
{%- set columns = [
    'partner_code', 'transaction_id', 'store_id', 'transaction_ts_utc', 'transaction_date_local',
    'amount', 'currency_code', 'status', 'customer_ref', 'batch_date', 'source_file', 'loaded_at',
    'source_row_number', 'source_file_modified_at', 'source_updated_at_utc'
] -%}

with new_records as (

    select
        '{{ var("partner_code") }}' as partner_code,
        transaction_id,
        store_id,
        -- The partner sends local wall-clock times. Both conversions name their zones, so the
        -- result does not depend on the session time zone.
        convert_timezone('{{ var("timezone") }}', 'UTC', transaction_ts) as transaction_ts_utc,
        transaction_ts::date as transaction_date_local,
        amount,
        coalesce(currency_code, '{{ var("currency_code") }}') as currency_code,
        status,
        customer_ref,
        batch_date,
        source_file,
        loaded_at,
        source_row_number,
        source_file_modified_at,
        convert_timezone('{{ var("timezone") }}', 'UTC', updated_at) as source_updated_at_utc
    from {{ ref('checked_sales_transaction') }}
    where array_size(reject_reasons) = 0
    {%- if is_incremental() %}
      and loaded_at >= (
          select dateadd(
              hour,
              -{{ var('raw_lookback_hours') }},
              coalesce(max(loaded_at), '1970-01-01'::timestamp_ntz)
          )
          from {{ this }}
      )
    {%- endif %}

),

candidates as (

    select {{ columns | join(', ') }} from new_records
    {%- if is_incremental() %}

    union all

    select {{ columns | join(', ') }}
    from {{ this }}
    where ({{ key }}) in (select {{ key }} from new_records)
    {%- endif %}

)

select {{ columns | join(', ') }}
from candidates
qualify row_number() over (partition by {{ key }} order by {{ current_version_first() }}) = 1
