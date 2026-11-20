{{ config(grants={'select': var('reader_roles')}) }}

-- Report 3. The stores that sold the most on each of the most recent dates that have sales.

with store_sales as (

    select
        transaction_date,
        store_id,
        sum(amount) as total_sales
    from ({{ completed_sales() }})
    group by transaction_date, store_id

),

ranked as (

    select
        *,
        dense_rank() over (order by transaction_date desc) as date_recency,
        -- row_number, not rank: two stores with the same sales take consecutive positions,
        -- ordered by store_id, so a date never has more than the configured number of rows.
        row_number() over (
            partition by transaction_date
            order by total_sales desc, store_id
        ) as sales_rank
    from store_sales

)

select
    {{ snapshot_date() }} as snapshot_date,
    ranked.transaction_date,
    ranked.sales_rank,
    ranked.total_sales,
    ranked.store_id,
    -- Null when the partner sold from a store it has not listed yet.
    stores.store_name
from ranked
left join {{ ref('std_store') }} as stores
    on stores.store_id = ranked.store_id
where ranked.date_recency <= {{ var('top_stores_report_dates') }}
  and ranked.sales_rank <= {{ var('top_stores_per_date') }}
