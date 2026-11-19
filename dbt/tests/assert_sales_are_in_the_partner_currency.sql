-- The reports add amounts up without converting them. A transaction in another currency
-- would be summed as if it were the partner's, so it fails the run until conversion exists.

select transaction_id, currency_code
from {{ ref('std_sales_transaction') }}
where currency_code <> '{{ var("currency_code") }}'
