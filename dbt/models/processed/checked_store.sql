{{ config(materialized='view') }}

{{ checked_records('store') }}
