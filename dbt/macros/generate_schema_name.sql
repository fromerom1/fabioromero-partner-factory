{#- Schemas are named exactly as configured (PROCESSED, ANALYTICS), not <target>_<schema>:
    each partner already has its own database per environment. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
