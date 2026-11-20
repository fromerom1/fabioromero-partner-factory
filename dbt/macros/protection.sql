{#- Statements that put the masking policy and the personal-data tag on customer_ref.

    Run as a post-hook of the model that owns the column, on every run: `--full-refresh`
    replaces the table, and a replaced table comes back without policy or tag. dbt runs
    post-hooks before it grants the table to the readers, so they never get to read a
    rebuilt table that is not protected yet. FORCE makes the statement valid whether or not
    the column already has the policy. -#}
{% macro protect_customer_ref() %}
    {%- set column = "alter table " ~ this ~ " modify column customer_ref" -%}
    {{ return([
        column ~ " set masking policy " ~ var('customer_ref_policy') ~ " force",
        column ~ " set tag " ~ var('pii_tag') ~ " = '" ~ var('customer_ref_tag_value') ~ "'",
    ]) }}
{% endmacro %}
