-- The latest snapshot of the sales report adds up to the completed transactions of the
-- standard model for the same dates.

with report as (

    select transaction_date, total_sales
    from {{ ref('rpt_daily_sales') }}
    qualify snapshot_date = max(snapshot_date) over ()

),

standard as (

    select transaction_date, sum(amount) as total_sales
    from ({{ completed_sales() }})
    group by transaction_date

)

select report.transaction_date, report.total_sales as reported, standard.total_sales as expected
from report
left join standard using (transaction_date)
where report.total_sales is distinct from standard.total_sales
