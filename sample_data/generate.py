"""Generates the fictional partner files under sample_data/.

Deterministic: the same seed always writes the same files, so the committed files and this
script never drift apart. Each folder mirrors the partner's landing prefix. The edge cases the
pipeline must survive are written explicitly below and listed in sample_data/README.md.

    python sample_data/generate.py
"""

from __future__ import annotations

import json
import random
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
RNG = random.Random(20261001)


def _uuid() -> str:
    return str(uuid.UUID(int=RNG.getrandbits(128), version=4))


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines):>4} lines  {path.relative_to(ROOT)}")


# --- Partner #1: MERCADOLOCAL_BR, CSV ---------------------------------------------------------

STORES_HEADER = "store_group,store_token,store_name"
SALES_HEADER = (
    "store_token,transaction_id,receipt_token,transaction_time,amount,source_id,user_role"
)


def _store(number: int) -> tuple[str, str, str]:
    group = f"{RNG.getrandbits(32):08X}"
    return group, group.lower() + _uuid()[8:], f"store {number:07d}"


def _sale(store_token: str, when: datetime, **overrides: str) -> dict[str, str]:
    sale = {
        "store_token": store_token,
        "transaction_id": _uuid(),
        "receipt_token": "".join(
            RNG.choices(string.ascii_letters + string.digits, k=RNG.randint(5, 12))
        ),
        "transaction_time": when.strftime("%Y%m%dT%H%M%S.000"),
        "amount": f"${RNG.randint(350, 48000) / 100:.2f}",
        "source_id": _uuid(),
        "user_role": RNG.choice(["Cashier", "Manager", "Self-checkout"]),
    }
    return sale | overrides


def _csv(row: dict[str, str]) -> str:
    return ",".join(f'"{value}"' if "," in value else value for value in row.values())


def _sales(stores: list[tuple[str, str, str]], day: datetime, count: int) -> list[dict[str, str]]:
    """Sales spread over the opening hours of ``day`` and the evening before."""
    return [
        _sale(RNG.choice(stores)[1], day + timedelta(minutes=RNG.randint(-300, 1260)))
        for _ in range(count)
    ]


def mercadolocal_br() -> dict:
    out = ROOT / "mercadolocal_br"
    stores = [_store(number) for number in range(1, 9)]
    lines = [",".join(store) for store in stores]

    # The store list is additive, and the header comes and goes.
    _write(out / "stores_20261001.csv", [STORES_HEADER, *lines[:6]])
    _write(out / "stores_20261002.csv", lines[:7])
    _write(
        out / "stores_20261003.csv",
        [
            STORES_HEADER,
            *lines[:8],
            "0000BEEF,NOT-A-UUID,store with an invalid token",
            f"{stores[2][0]},{stores[2][1]},",  # known store, name missing
            "0000C0DE",  # truncated line
        ],
    )

    day1, day2, day3 = (datetime(2026, 10, d) for d in (1, 2, 3))
    unknown_store = "ffffffff" + _uuid()[8:]

    first = _sales(stores[:6], day1, 30)
    corrected_later = first[4]
    _write(
        out / "sales_20261001.csv",
        [
            SALES_HEADER,
            *map(_csv, first),
            _csv(first[2]),  # the same line twice in one file
            _csv(_sale(stores[0][1], day1.replace(hour=11), amount="$1,250.00")),
            _csv(_sale(stores[1][1], day1.replace(hour=12), amount="")),
            _csv(_sale(stores[1][1], day1.replace(hour=13), amount="abc")),
            _csv(_sale(stores[3][1], day1, transaction_time="2026-10-01 17:46:00")),
            _csv(_sale(stores[4][1], day1.replace(hour=15), transaction_id="")),
            _csv(_sale(stores[6][1], day1.replace(hour=16))),  # store listed the day after
        ],
    )

    second = _sales(stores[:7], day2, 25)
    _write(
        out / "sales_20261002.csv",
        [
            *map(_csv, second),
            _csv(corrected_later | {"amount": "$19.90"}),  # re-sent with another amount
            _csv(_sale(stores[5][1], datetime(2026, 9, 27, 10, 15))),  # late arrival
            _csv(_sale(unknown_store, day2.replace(hour=9))),  # store never listed
            _csv(_sale(stores[0][1], datetime(2026, 10, 1, 23, 30))),  # next day in UTC
        ],
    )

    # A second file for the same batch date. One transaction_id repeats under another store:
    # the key is store_token + transaction_id, so both rows are distinct transactions.
    same_id_other_store = _sale(stores[3][1], day2.replace(hour=18)) | {
        "transaction_id": second[0]["transaction_id"]
    }
    _write(
        out / "sales_20261002_02.csv",
        [SALES_HEADER, *map(_csv, _sales(stores[:7], day2, 8)), _csv(same_id_other_store)],
    )

    third = _sales(stores, day3, 25)
    _write(
        out / "sales_20261003.csv",
        [
            SALES_HEADER,
            *map(_csv, third),
            ",".join(list(_sale(stores[2][1], day3).values())[:4]),  # too few fields
            _csv(_sale(stores[2][1], day3)) + ",unexpected",  # one field too many
        ],
    )
    return {"stores": stores, "first_day": first}


# --- Partner #2: TIENDAVIVA_ES, JSON ----------------------------------------------------------

SHOPS = [
    ("TV-0042", "Tienda Viva Malasaña", "Madrid", True),
    ("TV-0043", "Tienda Viva Gràcia", "Barcelona", False),
    ("TV-0044", "Tienda Viva Ruzafa", "Valencia", True),
    ("TV-0045", "Tienda Viva Triana", "Sevilla", True),
    ("TV-0046", "Tienda Viva Casco Viejo", "Bilbao", True),
    ("TV-0047", "Tienda Viva Vegueta", "Las Palmas de Gran Canaria", True),
]
CUSTOMERS = [
    {
        "email": f"{name}@example.com",
        "nif": f"{10000000 + 1111111 * i:08d}{'TRWAGMYFPDXBNJZSQVHLCKE'[i]}",
        "phone": f"+34 600 000 {i:03d}",
    }
    for i, name in enumerate(
        ["lucia.g", "marcos.r", "ines.v", "pablo.s", "nuria.c", "diego.m", "carla.t", "hugo.l"]
    )
]
_order_numbers = iter(range(184, 10_000))


def _order(when: datetime, shop: str | None = None, status: str = "COMPLETED") -> dict:
    items = [
        {
            "sku": f"{RNG.choice('ABCD')}-{RNG.randint(1, 30):02d}",
            "qty": RNG.randint(1, 3),
            "unit_cents": RNG.randint(250, 4500),
        }
        for _ in range(RNG.randint(1, 3))
    ]
    stamp = when.strftime("%d/%m/%Y %H:%M:%S")
    return {
        "event_id": f"e-{RNG.getrandbits(24):06x}",
        "order": {
            "id": f"ORD-2026-{next(_order_numbers):06d}",
            "created_at": stamp,
            "updated_at": stamp,
            "status": status,
        },
        "shop": {"code": shop or RNG.choice(SHOPS)[0]},
        "customer": RNG.choice(CUSTOMERS),
        "total_cents": sum(item["qty"] * item["unit_cents"] for item in items),
        "currency": "EUR",
        "items": items,
    }


def _update(event: dict, when: datetime, status: str) -> dict:
    """A later event of the same order."""
    order = event["order"] | {"updated_at": when.strftime("%d/%m/%Y %H:%M:%S"), "status": status}
    return event | {"event_id": f"e-{RNG.getrandbits(24):06x}", "order": order}


def _orders(day: datetime, count: int) -> list[dict]:
    return [_order(day + timedelta(minutes=RNG.randint(540, 1260))) for _ in range(count)]


def _jsonl(events: list[dict]) -> list[str]:
    return [json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in events]


def tiendaviva_es() -> dict:
    out = ROOT / "tiendaviva_es"
    snapshot = {
        "snapshot_month": "2026-10",
        "shops": [
            dict(zip(("code", "name", "city", "active"), shop, strict=True)) for shop in SHOPS
        ],
    }
    _write(out / "shops_202610.json", _jsonl([snapshot]))

    day1, day2 = datetime(2026, 10, 6), datetime(2026, 10, 7)

    first = _orders(day1, 14)
    no_customer = _order(day1.replace(hour=13))
    del no_customer["customer"]
    _write(
        out / "orders/2026/10/06/tiendaviva_orders_20261006_01.jsonl",
        _jsonl(
            [
                *first,
                _order(day1.replace(hour=12), status="PENDING"),
                no_customer,
                _order(day1.replace(hour=14), shop="TV-0099"),  # shop not in the snapshot
            ]
        ),
    )
    _write(
        out / "orders/2026/10/06/tiendaviva_orders_20261006_02.jsonl",
        _jsonl(
            [
                *_orders(day1, 8),
                first[3],  # the same event delivered again
                _order(day1.replace(hour=19), status="CANCELLED"),
            ]
        ),
    )

    amended = _order(day1.replace(hour=20))
    amended_v2 = _update(amended, day2.replace(hour=10), "COMPLETED") | {"total_cents": 4990}
    no_order_id = _order(day2.replace(hour=11))
    del no_order_id["order"]["id"]
    iso_dates = _order(day2.replace(hour=11))
    iso_dates["order"] |= {"created_at": "2026-10-07T11:00:00", "updated_at": "2026-10-07T11:00:00"}
    _write(
        out / "orders/2026/10/07/tiendaviva_orders_20261007_01.jsonl",
        _jsonl(
            [
                _update(first[0], day2.replace(hour=9, minute=15, second=3), "REFUNDED"),
                *_orders(day2, 10),
                _order(day2.replace(minute=30)),  # 00:30 in Madrid is the day before in UTC
                amended_v2,  # the newer version of this order arrives first
                no_order_id,
                iso_dates,
                _order(day2.replace(hour=12)) | {"total_cents": None},
            ]
        ),
    )
    _write(
        out / "orders/2026/10/07/tiendaviva_orders_20261007_02.jsonl",
        _jsonl([*_orders(day2, 6), amended]),  # the older version arrives last
    )
    return {"first_day": first, "amended": amended_v2}


# --- A later delivery ---------------------------------------------------------------------------
# Arrives after the first files have been processed, to exercise the incremental run. It uses
# its own random sequence, so adding to it never changes the files above.


def later_delivery(brazil: dict, spain: dict) -> None:
    global RNG
    RNG = random.Random(20261004)
    stores = brazil["stores"]
    day = datetime(2026, 10, 4)

    _write(
        ROOT / "mercadolocal_br/sales_20261004.csv",
        [
            SALES_HEADER,
            *map(_csv, _sales(stores, day, 6)),
            # Notations the declared money format does not accept: all invalid, none reread.
            *(
                _csv(_sale(stores[0][1], day.replace(hour=10), amount=amount))
                for amount in ("63,98", "1.250,00", "abc63.98", "1,2,3.45", "1e3", "63.987")
            ),
        ],
    )
    # A correction of a transaction processed days ago, plus one new sale. The partner first
    # re-sends it under the name of the file above, which Snowpipe does not load again, and
    # then under a new name.
    _write(
        ROOT / "mercadolocal_br/sales_20261004_02.csv",
        [
            SALES_HEADER,
            _csv(brazil["first_day"][10] | {"amount": "$25.00"}),
            _csv(_sale(stores[1][1], day.replace(hour=17))),
        ],
    )

    day = datetime(2026, 10, 8)
    amended = spain["amended"]
    older_version = _update(amended, datetime(2026, 10, 6, 21), "COMPLETED") | {"total_cents": 111}
    _write(
        ROOT / "tiendaviva_es/orders/2026/10/08/tiendaviva_orders_20261008_01.jsonl",
        _jsonl(
            [
                *_orders(day, 5),
                # A correction: newer than the stored version, so it replaces it.
                _update(spain["first_day"][1], day.replace(hour=9), "REFUNDED"),
                # Older than the stored version of this order: it must not replace it.
                older_version,
            ]
        ),
    )


# The order in which the partners deliver. "Last received wins" depends on it, so the files are
# uploaded one at a time in this order (see README.md).
DELIVERY_ORDER = [
    "mercadolocal_br/stores_20261001.csv",
    "mercadolocal_br/sales_20261001.csv",
    "mercadolocal_br/stores_20261002.csv",
    "mercadolocal_br/sales_20261002.csv",
    "mercadolocal_br/sales_20261002_02.csv",
    "mercadolocal_br/stores_20261003.csv",
    "mercadolocal_br/sales_20261003.csv",
    "tiendaviva_es/shops_202610.json",
    "tiendaviva_es/orders/2026/10/06/tiendaviva_orders_20261006_01.jsonl",
    "tiendaviva_es/orders/2026/10/06/tiendaviva_orders_20261006_02.jsonl",
    "tiendaviva_es/orders/2026/10/07/tiendaviva_orders_20261007_01.jsonl",
    "tiendaviva_es/orders/2026/10/07/tiendaviva_orders_20261007_02.jsonl",
    # Later delivery
    "mercadolocal_br/sales_20261004.csv",
    "mercadolocal_br/sales_20261004_02.csv",
    "tiendaviva_es/orders/2026/10/08/tiendaviva_orders_20261008_01.jsonl",
]


if __name__ == "__main__":
    later_delivery(mercadolocal_br(), tiendaviva_es())
    written = {
        str(path.relative_to(ROOT))
        for partner in ("mercadolocal_br", "tiendaviva_es")
        for path in (ROOT / partner).rglob("*")
        if path.is_file()
    }
    assert written == set(DELIVERY_ORDER), written ^ set(DELIVERY_ORDER)
    (ROOT / "delivery_order.txt").write_text("\n".join(DELIVERY_ORDER) + "\n")
