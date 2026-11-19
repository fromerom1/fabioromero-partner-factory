-- Every delivered record is either valid or rejected: none is lost and none is counted twice.

select *
from {{ ref('rpt_batch_processing') }}
where raw_records <> valid_records + rejected_records
