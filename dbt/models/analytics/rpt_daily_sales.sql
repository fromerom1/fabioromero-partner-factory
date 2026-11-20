{{ config(grants={'select': var('reader_roles')}) }}

-- Report 2. Sales by transaction date, for the most recent dates that have sales.

with daily as (

    select
        transaction_date,
        count(distinct store_id) as stores_with_sales,
        sum(amount) as total_sales,
        round(avg(amount), 2) as average_sale
    from ({{ completed_sales() }})
    group by transaction_date

),

with_month_to_date as (

    -- Accumulated over every date of the month, before the report is cut to its last dates:
    -- the first date kept still carries the sales of the earlier days of its month.
    select
        *,
        sum(total_sales) over (
            partition by date_trunc('month', transaction_date)
            order by transaction_date
        ) as month_to_date_sales
    from daily

)

select
    {{ snapshot_date() }} as snapshot_date,
    transaction_date,
    stores_with_sales,
    total_sales,
    average_sale,
    month_to_date_sales
from with_month_to_date
qualify row_number() over (order by transaction_date desc) <= {{ var('sales_report_dates') }}
