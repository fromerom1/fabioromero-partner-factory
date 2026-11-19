# Sample data

Fictional files for the two sample partners, written by `generate.py` (fixed seed, so the
script and the files never drift apart). Each folder mirrors the partner's landing prefix:
uploading `sample_data/<partner>/` to `s3://<bucket>/<partner>/landing/` reproduces a delivery.

| File | Lines | What it exercises |
|---|---|---|
| `mercadolocal_br/stores_20261001.csv` | 7 | Header present, six stores |
| `mercadolocal_br/stores_20261002.csv` | 7 | No header; one new store (additive list) |
| `mercadolocal_br/stores_20261003.csv` | 12 | Header; invalid store token, missing name, truncated line |
| `mercadolocal_br/sales_20261001.csv` | 38 | Header; same line twice, `$1,250.00` quoted amount, empty and non-numeric amount, timestamp in another format, missing transaction id, sale of a store listed the day after |
| `mercadolocal_br/sales_20261002.csv` | 29 | No header; a transaction of the previous file re-sent with another amount, a late sale from 27 September, a store never listed, a sale at 23:30 local time (next day in UTC) |
| `mercadolocal_br/sales_20261002_02.csv` | 10 | Second file of the same batch date; a transaction id repeated under a different store |
| `mercadolocal_br/sales_20261003.csv` | 28 | Header; a line with too few fields and a line with one field too many |
| `tiendaviva_es/shops_202610.json` | 1 | Monthly snapshot, one document; an inactive shop, non-ASCII names |
| `tiendaviva_es/orders/2026/10/06/tiendaviva_orders_20261006_01.jsonl` | 17 | A pending order, an order without customer data, a shop missing from the snapshot |
| `tiendaviva_es/orders/2026/10/06/tiendaviva_orders_20261006_02.jsonl` | 10 | The same event delivered twice, a cancelled order |
| `tiendaviva_es/orders/2026/10/07/tiendaviva_orders_20261007_01.jsonl` | 16 | A refund of an order of the day before, an order at 00:30 Madrid time (previous day in UTC), the newer version of an order arriving before the older one, a missing order id, ISO-formatted dates, a null total |
| `tiendaviva_es/orders/2026/10/07/tiendaviva_orders_20261007_02.jsonl` | 7 | The older version of that order arriving last |

## Delivering the files

`delivery_order.txt` lists the files in the order the partners would send them. Upload them one
at a time and a couple of seconds apart: the processor resolves duplicates by "last received",
which is the modification time of the file in the bucket, and files uploaded in one batch share
the same second.

```bash
BUCKET=fabioromero-partner-factory-dev   # aws.bucket in platform/dev.yml
while read -r file; do
  aws s3 cp "sample_data/$file" "s3://$BUCKET/${file%%/*}/landing/${file#*/}"
  sleep 2
done < sample_data/delivery_order.txt
```

Snowpipe loads each file within about a minute; `factory load-status <PARTNER>` shows them.

All customer data is invented: `example.com` addresses and made-up identity numbers.
