"""The standard model every partner is mapped to.

Types and requiredness are defined here, in code, and not in the partner files: a partner
configuration says where a value comes from and how it is written, never what it becomes.
Changing this module changes the contract for every partner, and is reviewed as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["text", "boolean", "timestamp", "amount", "status", "currency", "pseudonym"]

SQL_TYPES: dict[Kind, str] = {
    "text": "VARCHAR",
    "boolean": "BOOLEAN",
    "timestamp": "TIMESTAMP_NTZ",
    "amount": "NUMBER(11, 2)",
    "status": "VARCHAR",
    "currency": "VARCHAR",
    "pseudonym": "VARCHAR",
}

SALE_STATUSES = ("COMPLETED", "REFUNDED", "PENDING", "CANCELLED")

Transform = Literal["lower", "upper", "remove_spaces", "pseudonymize"]

# The catalog of transformations a partner file can ask for, and the kinds of field each one
# applies to. Anything outside the catalog is a code change, not configuration. Amounts and
# timestamps take no transforms: they are parsed against a declared format instead.
TRANSFORM_KINDS: dict[str, tuple[Kind, ...]] = {
    "lower": ("text", "status", "currency", "pseudonym"),
    "upper": ("text", "status", "currency", "pseudonym"),
    "remove_spaces": ("text", "pseudonym"),
    "pseudonymize": ("pseudonym",),
}


@dataclass(frozen=True)
class StandardField:
    name: str
    kind: Kind
    required: bool = True


@dataclass(frozen=True)
class Entity:
    name: str
    fields: tuple[StandardField, ...]

    def field(self, name: str) -> StandardField | None:
        return next((field for field in self.fields if field.name == name), None)


STORE = Entity(
    "store",
    (
        StandardField("store_id", "text"),
        StandardField("store_name", "text"),
        StandardField("is_active", "boolean", required=False),
    ),
)

SALES_TRANSACTION = Entity(
    "sales_transaction",
    (
        StandardField("transaction_id", "text"),
        StandardField("store_id", "text"),
        # Local wall-clock time of the sale; the processor derives the UTC time and local date.
        StandardField("transaction_ts", "timestamp"),
        StandardField("amount", "amount"),
        StandardField("status", "status"),
        # Falls back to the partner's currency when the partner does not send one.
        StandardField("currency_code", "currency", required=False),
        StandardField("customer_ref", "pseudonym", required=False),
        # When the partner versions its records, the latest version wins over the last received.
        StandardField("updated_at", "timestamp", required=False),
    ),
)

ENTITIES = {entity.name: entity for entity in (STORE, SALES_TRANSACTION)}
