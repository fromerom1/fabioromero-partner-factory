-- The incremental table must equal what a rebuild from all of RAW would produce: for every
-- key, the stored row is the current version among all valid records ever received, with the
-- same values in every column.
-- Guards the merge logic and the mapping: a lost record, a stale version, a leftover row or a
-- value computed with an older configuration all show up here.

{%- set key = var('sales_transaction').key | join(', ') %}
{%- set columns = sales_transaction_columns() | join(', ') %}

with expected as (

    select {{ columns }}
    from ({{ valid_sales_transactions() }})
    qualify row_number() over (partition by {{ key }} order by {{ current_version_first() }}) = 1

),

actual as (

    select {{ columns }}
    from {{ ref('std_sales_transaction') }}

)

select 'missing or different in std_sales_transaction' as problem, *
from (select * from expected except select * from actual)

union all

select 'unexpected in std_sales_transaction' as problem, *
from (select * from actual except select * from expected)
