{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=var('sales_transaction').key,
        on_schema_change='fail',
        post_hook=protect_customer_ref(),
        grants={'select': var('reader_roles')}
    )
}}

{#-
    One row per transaction: the current version of each key.

    Incremental and idempotent. Each run reads the valid records loaded since the newest one
    already processed, minus a lookback window, and puts them next to the rows the table
    already holds for the same keys. The current version is then chosen among all of them, so
    a record that arrives late but is older than the stored one never replaces it, and running
    twice gives the same table.

    When a stored row and a record read again are the same delivery, the one just computed
    wins, so a change of mapping reaches the rows inside the window. Rows older than the window
    need `--full-refresh`; assert_incremental_matches_full_recompute fails until then.
-#}

{%- set key = var('sales_transaction').key | join(', ') -%}
{%- set columns = sales_transaction_columns() | join(', ') -%}

with new_records as (

    {{ valid_sales_transactions() }}
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

    select {{ columns }}, true as is_recomputed from new_records
    {%- if is_incremental() %}

    union all

    select {{ columns }}, false as is_recomputed
    from {{ this }}
    where ({{ key }}) in (select {{ key }} from new_records)
    {%- endif %}

)

select {{ columns }}
from candidates
qualify row_number() over (
    partition by {{ key }}
    order by {{ current_version_first() }}, is_recomputed desc
) = 1
