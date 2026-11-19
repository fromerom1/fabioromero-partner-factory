-- The incremental table must equal what a rebuild from all of RAW would produce: for every
-- key, the row stored is the current version among all valid records ever received.
-- Guards the merge logic: a lost record, a stale version or a leftover row all show up here.

{%- set key = var('sales_transaction').key | join(', ') %}

with expected as (

    select
        {{ key }},
        source_file,
        source_row_number,
        source_file_modified_at,
        convert_timezone('{{ var("timezone") }}', 'UTC', updated_at) as source_updated_at_utc
    from {{ ref('checked_sales_transaction') }}
    where array_size(reject_reasons) = 0
    qualify row_number() over (partition by {{ key }} order by {{ current_version_first() }}) = 1

),

actual as (

    select {{ key }}, source_file, source_row_number
    from {{ ref('std_sales_transaction') }}

)

select 'missing or stale in std_sales_transaction' as problem, {{ key }}, source_file, source_row_number
from (
    select {{ key }}, source_file, source_row_number from expected
    except
    select {{ key }}, source_file, source_row_number from actual
)

union all

select 'unexpected in std_sales_transaction' as problem, {{ key }}, source_file, source_row_number
from (
    select {{ key }}, source_file, source_row_number from actual
    except
    select {{ key }}, source_file, source_row_number from expected
)
