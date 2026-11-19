-- One row per store, recomputed on every run from everything the partner has sent.
-- Attributes come from the last record received; first and last appearance from batch dates.

with valid as (

    select *
    from {{ ref('checked_store') }}
    where array_size(reject_reasons) = 0

)

select
    '{{ var("partner_code") }}' as partner_code,
    store_id,
    store_name,
    '{{ var("country_code") }}' as country_code,
    is_active,
    min(batch_date) over (partition by store_id) as first_seen_at,
    max(batch_date) over (partition by store_id) as last_seen_at
from valid
qualify row_number() over (
    partition by store_id
    order by {{ last_received_first() }}, source_element_index desc
) = 1
