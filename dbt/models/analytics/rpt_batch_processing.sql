{{ config(grants={'select': var('reader_roles')}) }}

-- Report 1. Processing by batch date: what the partner delivered and what became of it.
-- Counts every source of the partner. A record is either valid or rejected; a valid record
-- later superseded by a newer one still counts as valid. Header lines are not records.

with records as (

    {% for entity in ['store', 'sales_transaction'] %}
    select batch_date, loaded_at, array_size(reject_reasons) = 0 as is_valid
    from {{ ref('checked_' ~ entity) }}
    {{ "union all" if not loop.last }}
    {% endfor %}

),

by_batch_date as (

    select
        batch_date,
        count(*) as raw_records,
        count_if(is_valid) as valid_records,
        count_if(not is_valid) as rejected_records,
        max(loaded_at)::date as processing_date
    from records
    where batch_date is not null
    group by batch_date

)

select
    {{ snapshot_date() }} as snapshot_date,
    batch_date,
    raw_records,
    valid_records,
    rejected_records,
    processing_date
from by_batch_date
qualify row_number() over (order by batch_date desc) <= {{ var('batch_report_dates') }}
