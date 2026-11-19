-- Every record that failed validation, with the reasons. Rebuilt on every run from RAW.
-- The record itself is not copied: it stays in RAW, the only schema that holds partner data as
-- delivered, and is found there by source_file and source_row_number.

{% for entity in ['store', 'sales_transaction'] %}
select
    '{{ var("partner_code") }}' as partner_code,
    '{{ entity }}' as entity,
    source_file,
    source_row_number,
    source_element_index,
    batch_date,
    loaded_at,
    reject_reasons
from {{ ref('checked_' ~ entity) }}
where array_size(reject_reasons) > 0
{{ "union all" if not loop.last }}
{% endfor %}
