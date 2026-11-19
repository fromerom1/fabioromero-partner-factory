{#-
    Reads one standard entity from the partner's raw table and judges every record.

    The result has one row per delivered record (headers excluded), with the standard fields
    typed, where the record came from, and `reject_reasons`: empty when the record is valid.
    A required field that is absent gives `missing_<field>`; a value that is present but cannot
    be converted gives `invalid_<field>`. Nothing here can fail on bad data.

    `entity` names a variable compiled by the factory from the partner file (see
    src/partner_factory/processor.py and `factory dbt-vars <PARTNER>`).
-#}
{% macro checked_records(entity) %}
{%- set spec = var(entity) -%}
with extracted as (

    select
        src.SOURCE_FILE as source_file,
        src.FILE_ROW_NUMBER as source_row_number,
        {{ 'item.INDEX' if spec.explode else 'null' }}::integer as source_element_index,
        src.FILE_LAST_MODIFIED as source_file_modified_at,
        convert_timezone('UTC', src.LOADED_AT)::timestamp_ntz as loaded_at,
        {{ spec.batch_date }} as batch_date,
        {{ spec.has_extra_field }} as has_extra_field,
        {%- for field in spec.fields %}
        {{ field.raw }} as {{ field.name }}__raw{{ "," if not loop.last }}
        {%- endfor %}
    from {{ target.database }}.RAW.{{ spec.raw_table }} as src
    {%- if spec.explode %}
    -- OUTER keeps a record whose array is missing, so it is rejected instead of vanishing.
    , lateral flatten(input => {{ spec.explode }}, outer => true) as item
    {%- endif %}
    where not coalesce({{ spec.is_header }}, false)

),

typed as (

    select
        *,
        {%- for field in spec.fields %}
        {{ field.value }} as {{ field.name }}{{ "," if not loop.last }}
        {%- endfor %}
    from extracted

)

select
    source_file,
    source_row_number,
    source_element_index,
    source_file_modified_at,
    loaded_at,
    batch_date,
    {%- for field in spec.fields %}
    {{ field.name }},
    {%- endfor %}
    array_construct_compact(
        iff(has_extra_field, 'unexpected_extra_field', null),
        iff(batch_date is null, 'invalid_batch_date', null)
        {%- for field in spec.fields %},
        case
            when {{ field.name }}__raw is null then {{ "'missing_" ~ field.name ~ "'" if field.required else "null" }}
            when {{ field.name }} is null then 'invalid_{{ field.name }}'
        end
        {%- endfor %}
    ) as reject_reasons
from typed
{% endmacro %}


{#- "Last received wins": the file delivered last, then the path, then the position in the
    file. The path only breaks ties between files delivered in the same instant. -#}
{% macro last_received_first() -%}
    source_file_modified_at desc, source_file desc, source_row_number desc
{%- endmacro %}


{#- Which of several records of the same transaction is current: the latest version according
    to the partner when it sends one, otherwise the last one received. -#}
{% macro current_version_first() -%}
    source_updated_at_utc desc nulls last, {{ last_received_first() }}
{%- endmacro %}


{#- The standard columns of a sales transaction, in table order. -#}
{% macro sales_transaction_columns() -%}
    {{ return([
        'partner_code', 'transaction_id', 'store_id', 'transaction_ts_utc',
        'transaction_date_local', 'amount', 'currency_code', 'status', 'customer_ref',
        'batch_date', 'source_file', 'loaded_at', 'source_row_number',
        'source_file_modified_at', 'source_updated_at_utc'
    ]) }}
{%- endmacro %}


{#-
    Valid sales records in standard form. The one definition of how a checked record becomes a
    standard row: the incremental model and the test that recomputes it both use it.

    The partner sends local wall-clock times. `timezone_sql` gives the zone of each record from
    its store (the partner's zone, or the exception declared for that store), and both
    conversions name source and target, so nothing depends on the session time zone.
-#}
{% macro valid_sales_transactions() %}
    select
        '{{ var("partner_code") }}' as partner_code,
        transaction_id,
        store_id,
        convert_timezone({{ var("timezone_sql") }}, 'UTC', transaction_ts) as transaction_ts_utc,
        transaction_ts::date as transaction_date_local,
        amount,
        coalesce(currency_code, '{{ var("currency_code") }}') as currency_code,
        status,
        customer_ref,
        batch_date,
        source_file,
        loaded_at,
        source_row_number,
        source_file_modified_at,
        convert_timezone({{ var("timezone_sql") }}, 'UTC', updated_at) as source_updated_at_utc
    from {{ ref('checked_sales_transaction') }}
    where array_size(reject_reasons) = 0
{% endmacro %}
