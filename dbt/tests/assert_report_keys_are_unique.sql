-- Each report has one row per snapshot and business key. Running twice on the same day must
-- replace the snapshot, never add to it.

select 'rpt_batch_processing' as report, snapshot_date, batch_date::varchar as report_key, count(*) as records
from {{ ref('rpt_batch_processing') }}
group by all
having count(*) > 1

union all

select 'rpt_daily_sales', snapshot_date, transaction_date::varchar, count(*)
from {{ ref('rpt_daily_sales') }}
group by all
having count(*) > 1

union all

select 'rpt_top_stores', snapshot_date, transaction_date || ' #' || sales_rank, count(*)
from {{ ref('rpt_top_stores') }}
group by all
having count(*) > 1
