-- The key configured for the partner identifies exactly one row.

{%- set key = var('sales_transaction').key | join(', ') %}

select {{ key }}, count(*) as records
from {{ ref('std_sales_transaction') }}
group by {{ key }}
having count(*) > 1
