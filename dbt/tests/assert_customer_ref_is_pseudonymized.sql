-- No customer identifier reaches the standard model in clear: customer_ref is always a
-- SHA-256 digest.

select transaction_id, customer_ref
from {{ ref('std_sales_transaction') }}
where customer_ref is not null
  and not regexp_like(customer_ref, '[0-9a-f]{64}')
