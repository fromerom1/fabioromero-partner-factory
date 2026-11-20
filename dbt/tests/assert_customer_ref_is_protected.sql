-- customer_ref carries its masking policy and its personal-data tag after every run,
-- a full refresh included. Returns one row per protection that is missing.

with policies as (

    select policy_db || '.' || policy_schema || '.' || policy_name as policy
    from table(
        {{ target.database }}.information_schema.policy_references(
            ref_entity_name => '{{ ref("std_sales_transaction") }}',
            ref_entity_domain => 'table'
        )
    )
    where ref_column_name = 'CUSTOMER_REF'

),

tags as (

    select tag_database || '.' || tag_schema || '.' || tag_name as tag, tag_value
    from table(
        {{ target.database }}.information_schema.tag_references_all_columns(
            '{{ ref("std_sales_transaction") }}', 'table'
        )
    )
    where column_name = 'CUSTOMER_REF'

)

select 'masking policy' as missing
where not exists (select 1 from policies where policy = '{{ var("customer_ref_policy") }}')

union all

select 'personal-data tag' as missing
where not exists (
    select 1
    from tags
    where tag = '{{ var("pii_tag") }}'
      and tag_value = '{{ var("customer_ref_tag_value") }}'
)
