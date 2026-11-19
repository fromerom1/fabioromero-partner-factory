{{ config(materialized='view') }}

{{ checked_records('sales_transaction') }}
