# Partner onboarding runbook

For an engineer who has never onboarded a partner. It takes you from the partner's sample
files to a configuration that is reviewed, deployed and validated in DEV, and covers what to do
when something fails, how to roll back and how to remove a partner.

Onboarding a partner adds one file, `partners/<partner>.yml`. If you find yourself editing
Python, SQL templates or dbt models, stop: either the partner needs something the factory does
not offer yet, which is a separate change with its own review, or the answer is already in the
configuration options below.

**Conventions.** `<PARTNER>` is the partner code in upper case (`TIENDAVIVA_ES`), `<partner>`
the same in lower case, and the partner's database is `<PARTNER>_DEV`. Commands run from the
repository root after the setup in the [README](../README.md), with
`SNOWFLAKE_PRIVATE_KEY_FILE` exported. SQL runs as the pipeline role
(`PARTNER_FACTORY_DEV_PIPELINE`) in a Snowflake worksheet or any SQL client.

**Every command and statement here was run in DEV as written.** The walkthrough, the failure
cases and the decommissioning were rehearsed end to end with a throwaway partner,
`DEMOSHOP_CO`, which was then removed. Where a command names it, replace it with your partner.
The figures are the ones measured. The two exceptions are marked where they appear: one
branch of the procedure in 4.3, and the order of the two commands in 4.6.

---

## 1. What you need from the partner

Do not start the configuration until you have all four. Each answer below ends up as a key in
the partner file, so a missing answer is a guess in production.

### Sample files

- At least one real file per kind of file they will send, produced by the system that will
  produce them in production, not written by hand for you.
- Ask for the awkward ones: the first file of a month, a day with corrections, a file with
  and without a header if both can happen.
- If the files contain personal data, agree how the samples reach you before they are sent.
  They must not be attached to a ticket or committed to the repository.

### Data dictionary

| Ask | Why | Becomes |
|---|---|---|
| Every field: name, meaning, optional or not | To map the standard model | `standard_model.*.fields` |
| What identifies a store, and a transaction. Is a transaction id unique across the partner or only within a store? | Duplicates collapse on this key | `sales_transaction.key` |
| How timestamps are written, and in which time zone. Any store in another zone? | Times are stored in UTC; a wrong zone shifts sales to another day | `format`, `timezone`, `store_timezones` |
| How amounts are written: units or cents, currency symbol, thousands and decimal separators | `63,98` is 63.98 in one notation and 6,398 in another | `money` |
| Currency. One for the partner, or one per record? | | `currency_code`, `fields.currency_code` |
| Every status a transaction can have, and what each means | Only `COMPLETED` counts as a sale in the reports | `status` with `value_map` or `constant` |
| Is a record sent again when it changes? Is there a version or update time? | Decides which copy wins | `fields.updated_at` |
| Which fields are personal data | Raw records are tagged; identifiers leave RAW only pseudonymized | `personal_data`, `fields.customer_ref` |

### Delivery method

- **Where.** Files are delivered to `s3://<bucket>/<partner>/landing/`. Subfolders are fine.
  In this assessment the engineer uploads the files with `aws s3 cp`, standing in for the
  partner. Giving a partner write access to its prefix is not built.
- **File names.** The name must carry the batch date (`sales_20261001.csv`), and names may only
  use letters, digits and `_ . / = -`.
- **A name is never reused.** Snowpipe loads a name once. A correction or a second file for the
  same date needs a new name, for example a suffix: `sales_20261001_02.csv`. Agree this in
  writing: it is the most common cause of "we sent it and you do not have it".
- **How often**, at what time, and whether each file is complete (a full list every time) or
  only what is new.
- **Encoding and CSV layout**: delimiter, quoting, and whether a header line is sent. A header
  is neither required nor a problem: it is recognised and dropped.

### Contacts

- Technical contact: who answers when a file is malformed or missing.
- Data owner: who can say what a field means and confirm totals.
- Escalation contact and their hours.
- For partners with personal data: their privacy contact, and for EU partners confirmation of
  where the data may be stored.

---

## 2. From sample files to a validated configuration in DEV

### Step 1. Write `partners/<partner>.yml`

Work on a branch. The file name must be the partner code in lower case. Start from the shipped
partner that looks most like yours: `partners/mercadolocal_br.yml` for CSV,
`partners/tiendaviva_es.yml` for JSON.

```yaml
partner_code: TIENDAVIVA_ES          # upper case; the database will be TIENDAVIVA_ES_DEV
display_name: TiendaViva (Spain)
country_code: ES                     # ISO 3166-1 alpha-2
currency_code: EUR                   # ISO 4217; used when a record carries no currency
timezone: Europe/Madrid              # IANA zone of the partner's local times
store_timezones:                     # optional: stores in another zone, by store_id
  TV-0047: Atlantic/Canary

sources:                             # one entry per kind of file; each becomes a raw table and a pipe
  orders:
    description: Order events, line-delimited JSON.
    format: json                     # json or csv
    file_pattern: "orders/[0-9]{4}/[0-9]{2}/[0-9]{2}/tiendaviva_orders_[0-9]{8}_[0-9]+[.]jsonl"
    example_file: orders/2026/10/06/tiendaviva_orders_20261006_01.jsonl
    batch_date: {regex: "tiendaviva_orders_([0-9]{8})_", format: YYYYMMDD}
    personal_data: true              # the records carry customer data

standard_model:
  store:
    source: shops
    explode: shops                   # optional: the array inside one document that holds the rows
    fields:
      store_id: {from: code}
      store_name: {from: name}
  sales_transaction:
    source: orders
    key: [transaction_id]            # or [store_id, transaction_id]
    fields:
      transaction_id: {from: order.id}
      store_id: {from: shop.code}
      transaction_ts: {from: order.created_at, format: "DD/MM/YYYY HH24:MI:SS"}
      amount: {from: total_cents, money: {unit: cents}}
      status: {from: order.status}
      customer_ref: {from: customer.nif, transforms: [upper, remove_spaces, pseudonymize]}
```

The options, all of them:

| Key | Values | Notes |
|---|---|---|
| `sources.<name>.file_pattern` | Regular expression | Matched against the whole path below `landing/`. Two sources must not match the same file. |
| `sources.<name>.example_file` | A path | Must match `file_pattern`; validation uses it to prove the pattern and the batch date work. |
| `sources.<name>.batch_date` | `regex` with one group, `format` `YYYYMMDD` or `YYYYMM` | Read from the file path. |
| `sources.<name>.csv` | `columns` (in file order), `delimiter` (`,` `;` `\|` tab), `quote` (`"` or `none`), `encoding` | Required for `csv`, not allowed for `json`. |
| `sources.<name>.personal_data` | `true` / `false` | Tags the raw record. Required on a source that feeds `customer_ref`. |
| `fields.<field>.from` | A key, or a dotted path for JSON | Where the value is in the record. |
| `fields.<field>.constant` | Text | For a field the partner does not send, such as a status that is always `COMPLETED`. |
| `fields.<field>.transforms` | `lower`, `upper`, `remove_spaces`, `pseudonymize` | Applied in order. `pseudonymize` must be last on `customer_ref`. |
| `fields.<field>.format` | A Snowflake timestamp format | Required on timestamps. Times are the partner's local time. |
| `fields.<field>.money` | `unit` (`units` or `cents`), `symbol`, `thousands`, `decimal` | Required on `amount`. A value outside the declared notation is rejected, never reinterpreted. |
| `fields.<field>.matches` | Regular expression | A text value that does not match completely is rejected. |
| `fields.<field>.value_map` | Partner value to standard value | For statuses. Standard statuses: `COMPLETED`, `REFUNDED`, `PENDING`, `CANCELLED`. |

Standard fields. Required: `store_id`, `store_name`; `transaction_id`, `store_id`,
`transaction_ts`, `amount`, `status`. Optional: `is_active`, `currency_code`, `customer_ref`,
`updated_at`.

Three decisions deserve a second look, because a wrong answer does not fail, it produces wrong
numbers:

- **`key`.** If transaction ids repeat across stores and the key is only `[transaction_id]`,
  sales of different stores overwrite each other.
- **`timezone`.** Reports group sales by local date.
- **`money`.** Declare exactly what the samples show. If the partner writes amounts two ways,
  that is a question for the partner, not a looser format.

### Step 2. Validate

```bash
uv run factory validate
```

```
ok    partners/demoshop_co.yml  DEMOSHOP_CO: stores, sales
ok    partners/mercadolocal_br.yml  MERCADOLOCAL_BR: stores, sales
ok    partners/tiendaviva_es.yml  TIENDAVIVA_ES: orders, shops
```

Needs no connection. It rejects, among others: unknown keys (a typo fails instead of being
ignored), a pattern that does not match its example file, two sources that would claim the
same file, a required standard field that is not mapped, a field read from a column that does
not exist, an option that does not fit the kind of field, an unknown time zone, a customer
identifier that is not pseudonymized, and a source that feeds `customer_ref` without
`personal_data: true`.

### Step 3. Read what the file generates

```bash
uv run factory deploy DEMOSHOP_CO --dry-run
uv run factory dbt-vars DEMOSHOP_CO
```

The first prints the SQL that will run in Snowflake; the second, the expressions dbt will use
for each field. Read them once. They are short, and this is where a wrong column order or a
wrong format is cheapest to catch.

### Step 4. Pull request

Open a pull request with the partner file. CI runs the linter, `factory validate` and the
tests. The reviewer checks the three decisions of step 1, that every source with personal data
says so, and that the samples are not in the repository.

### Step 5. Deploy to DEV

```bash
uv run factory deploy DEMOSHOP_CO
```

Creates the database, the three schemas, the two stages, the pseudonymization key, the `PII`
tag, the masking policy, the reader grants, and a file format, raw table and pipe per source.
It takes a few seconds and needs no Terraform: the bucket, the role and the notification are
shared by every partner.

Running it again changes nothing. It only ever creates what is missing.

### Step 6. Deliver the sample files

One file at a time and a couple of seconds apart: when two records have the same key, the one
received last wins, and "received" is the time of the file in the bucket.

```bash
BUCKET=fabioromero-partner-factory-dev   # aws.bucket in platform/dev.yml
aws s3 cp sample_data/mercadolocal_br/stores_20261001.csv "s3://$BUCKET/demoshop_co/landing/stores_20261001.csv"
sleep 2
aws s3 cp sample_data/mercadolocal_br/sales_20261001.csv "s3://$BUCKET/demoshop_co/landing/sales_20261001.csv"
```

### Step 7. Confirm the load

Snowpipe loads a file about a minute after it lands.

```bash
uv run factory load-status DEMOSHOP_CO
```

```
PIPE                             STATE    PENDING_FILES
DEMOSHOP_CO_DEV.RAW.STORES_PIPE  RUNNING  0
DEMOSHOP_CO_DEV.RAW.SALES_PIPE   RUNNING  0

SOURCE  SOURCE_FILE                              RECORDS  LAST_ROW  LOADED_AT_UTC
sales   demoshop_co/landing/sales_20261001.csv   38       38        2026-11-20 03:16:22
stores  demoshop_co/landing/stores_20261001.csv  7        7         2026-11-20 03:16:22

In landing (processed files are moved to historical by `factory archive`):
FILE                 STATE
sales_20261001.csv   loaded
stores_20261001.csv  loaded
```

`RECORDS` must equal the number of lines of the file (`wc -l`), header included: RAW keeps
every line. Every file must be `loaded`. The other states are explained in section 4.

### Step 8. Build the standard model and the reports

```bash
uv run factory dbt DEMOSHOP_CO build
```

```
Done. PASS=62 WARN=1 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=63
```

That is 8 models and 55 tests. A failing test stops the models that depend on it. The one
warning in the sample data is a sale whose store is not in the store list; see section 4.

### Step 9. Validate

Section 3. Do not skip it because the build is green: the tests prove the pipeline is
consistent with itself, not that the mapping is right.

### Step 10. Archive, and let the schedule take over

```bash
uv run factory archive DEMOSHOP_CO
```

This is what runs every day: it lists the loaded files, builds, and if every test passes moves
those files to `historical/`. Once the pull request is merged, Airflow shows a new DAG,
`partner_<partner>`, with no further work. It starts paused.

---

## 3. Validation before go-live

Four checks. Each has a command or a query and says what to compare it with. Replace
`MERCADOLOCAL_BR_DEV` with your database. The results shown are the ones measured in DEV.

### 3.1 Row counts

**Lines delivered equal lines loaded.** Compare `wc -l` of each file with `RECORDS` in
`uv run factory load-status <PARTNER>`.

**Every record is either valid or rejected.** Nothing disappears between RAW and the standard
model. Header lines are not records, so these counts are lower than the line counts.

```sql
SELECT entity,
       COUNT(*) AS records,
       COUNT_IF(ARRAY_SIZE(reject_reasons) = 0) AS valid,
       COUNT_IF(ARRAY_SIZE(reject_reasons) > 0) AS rejected
FROM (
    SELECT 'store' AS entity, reject_reasons FROM MERCADOLOCAL_BR_DEV.PROCESSED.CHECKED_STORE
    UNION ALL
    SELECT 'sales_transaction', reject_reasons FROM MERCADOLOCAL_BR_DEV.PROCESSED.CHECKED_SALES_TRANSACTION
)
GROUP BY entity
ORDER BY entity;
```

| | Entity | Records | Valid | Rejected |
|---|---|---|---|---|
| `MERCADOLOCAL_BR` | sales_transaction | 116 | 104 | 12 |
| | store | 24 | 21 | 3 |
| `TIENDAVIVA_ES` | sales_transaction | 57 | 54 | 3 |
| | store | 6 | 6 | 0 |

**What reached the standard model.** Fewer rows than valid records is expected: duplicates and
re-sent records collapse into one row per key. Explain the difference before going live.

```sql
SELECT (SELECT COUNT(*) FROM MERCADOLOCAL_BR_DEV.PROCESSED.STD_STORE) AS stores,
       (SELECT COUNT(*) FROM MERCADOLOCAL_BR_DEV.PROCESSED.STD_SALES_TRANSACTION) AS transactions;
```

`MERCADOLOCAL_BR`: 8 stores, 101 transactions. `TIENDAVIVA_ES`: 6 stores, 49 transactions.

**Per batch date, as report 1 shows it.** This is the view the partner can check file by file.

```sql
SELECT batch_date, raw_records, valid_records, rejected_records, processing_date
FROM MERCADOLOCAL_BR_DEV.ANALYTICS.RPT_BATCH_PROCESSING
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM MERCADOLOCAL_BR_DEV.ANALYTICS.RPT_BATCH_PROCESSING)
ORDER BY batch_date;
```

| Batch date | Raw | Valid | Rejected |
|---|---|---|---|
| 2026-10-01 | 43 | 39 | 4 |
| 2026-10-02 | 45 | 45 | 0 |
| 2026-10-03 | 38 | 33 | 5 |
| 2026-10-04 | 14 | 8 | 6 |

### 3.2 Totals

Ask the partner for their own totals for the sample period: number of sales and amount per
day. Compare them with the standard model.

```sql
SELECT transaction_date_local,
       COUNT(*) AS completed_sales,
       SUM(amount) AS total_amount,
       MIN(currency_code) AS currency
FROM MERCADOLOCAL_BR_DEV.PROCESSED.STD_SALES_TRANSACTION
WHERE status = 'COMPLETED'
GROUP BY transaction_date_local
ORDER BY transaction_date_local;
```

| Local date | Completed sales | Total (BRL) |
|---|---|---|
| 2026-09-27 | 1 | 467.24 |
| 2026-09-30 | 6 | 1,165.97 |
| 2026-10-01 | 31 | 8,045.41 |
| 2026-10-02 | 31 | 7,878.65 |
| 2026-10-03 | 27 | 6,195.95 |
| 2026-10-04 | 5 | 1,315.73 |

If they do not match, look in this order: rejected records (3.3), the time zone (sales near
midnight land on another day), the amount notation, and the key.

Report 2 must show the same amounts:

```sql
SELECT transaction_date, stores_with_sales, total_sales, average_sale, month_to_date_sales
FROM MERCADOLOCAL_BR_DEV.ANALYTICS.RPT_DAILY_SALES
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM MERCADOLOCAL_BR_DEV.ANALYTICS.RPT_DAILY_SALES)
ORDER BY transaction_date;
```

### 3.3 Data quality

**The tests.**

```bash
uv run factory dbt TIENDAVIVA_ES test
```

```
Done. PASS=54 WARN=1 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=55
```

They cover, among others: the transaction key is unique, records delivered equal valid plus
rejected, an incremental run gives the same table as a full recompute, the reports add up to
the standard model, every sale is in the partner's currency, and times are computed in UTC.

**Why records were rejected.** A required field that is absent gives `missing_<field>`; a
value that is present but cannot be read gives `invalid_<field>`. There are also
`unexpected_extra_field` (a CSV line with more fields than columns) and `invalid_batch_date`.

```sql
SELECT entity, reason.value::VARCHAR AS reason, COUNT(*) AS records
FROM MERCADOLOCAL_BR_DEV.PROCESSED.REJECTED_RECORD,
     LATERAL FLATTEN(input => reject_reasons) AS reason
GROUP BY entity, reason
ORDER BY records DESC, reason;
```

| Entity | Reason | Records |
|---|---|---|
| sales_transaction | invalid_amount | 7 |
| sales_transaction | missing_amount | 2 |
| store | missing_store_name | 2 |
| store | invalid_store_id | 1 |
| sales_transaction | invalid_transaction_ts | 1 |
| store | missing_store_id | 1 |
| sales_transaction | missing_transaction_id | 1 |
| sales_transaction | unexpected_extra_field | 1 |

A reason that hits most of the records of a file is almost always the configuration, not the
data: a wrong timestamp format rejects everything.

**The rejected record as the partner sent it.** Rejected records are not copied out of RAW;
they are found there by file and row. Run this only where reading RAW is appropriate: for a
source with personal data it shows that data.

```sql
SELECT r.source_file, r.source_row_number, r.reject_reasons, raw.record
FROM DEMOSHOP_CO_DEV.PROCESSED.REJECTED_RECORD AS r
JOIN DEMOSHOP_CO_DEV.RAW.SALES AS raw
  ON raw.source_file = r.source_file
 AND raw.file_row_number = r.source_row_number
WHERE r.entity = 'sales_transaction'
ORDER BY r.source_file, r.source_row_number
LIMIT 2;
```

**Statuses, and sales of stores the partner has not listed.**

```sql
SELECT t.status, COUNT(*) AS transactions, COUNT_IF(s.store_id IS NULL) AS store_not_listed
FROM TIENDAVIVA_ES_DEV.PROCESSED.STD_SALES_TRANSACTION AS t
LEFT JOIN TIENDAVIVA_ES_DEV.PROCESSED.STD_STORE AS s ON s.store_id = t.store_id
GROUP BY t.status
ORDER BY t.status;
```

| Status | Transactions | Store not listed |
|---|---|---|
| CANCELLED | 1 | 0 |
| COMPLETED | 45 | 1 |
| PENDING | 1 | 0 |
| REFUNDED | 2 | 0 |

Agree with the partner, before go-live, what share of rejected records is normal for them.
Nothing alerts on it today.

### 3.4 PII tagging and access

**The check.**

```bash
uv run factory check-access TIENDAVIVA_ES
```

It opens one session per role, with secondary roles off, and prints counts only, never a value.
It exits with an error if anything differs from the rules.

```
OBJECT                               PARTNER_FACTORY_DEV_PIPELINE  PARTNER_FACTORY_DEV_ANALYST  PARTNER_FACTORY_DEV_FRAUD_CREDIT
RAW.ORDERS                           read                          denied                       denied
RAW.SHOPS                            read                          denied                       denied
RAW.PSEUDONYMIZATION_KEY             read                          denied                       denied
PROCESSED.CHECKED_STORE              read                          denied                       denied
PROCESSED.CHECKED_SALES_TRANSACTION  read                          denied                       denied
PROCESSED.REJECTED_RECORD            read                          denied                       denied
PROCESSED.STD_STORE                  read                          read                         read
PROCESSED.STD_SALES_TRANSACTION      read                          read                         read
ANALYTICS.RPT_BATCH_PROCESSING       read                          read                         read
ANALYTICS.RPT_DAILY_SALES            read                          read                         read
ANALYTICS.RPT_TOP_STORES             read                          read                         read
customer_ref values                  visible (48 of 49 rows)       none visible (0 of 49 rows)  visible (48 of 49 rows)

  ok       tag PII = CUSTOMER_DATA on RAW.ORDERS.RECORD
  ok       tag PII = PSEUDONYMIZATION_KEY on RAW.PSEUDONYMIZATION_KEY.KEY_VALUE
  ok       tag PII = PSEUDONYM on PROCESSED.STD_SALES_TRANSACTION.CUSTOMER_REF
  ok       masking policy CUSTOMER_REF_MASK on PROCESSED.STD_SALES_TRANSACTION.CUSTOMER_REF

Everything is as expected.
```

A partner that sends no customer identifier, like `MERCADOLOCAL_BR`, shows `none visible`
for every role; the policy and the tags are in place anyway.

**The tag on a raw table.**

```sql
SELECT column_name, tag_name, tag_value, level
FROM TABLE(TIENDAVIVA_ES_DEV.INFORMATION_SCHEMA.TAG_REFERENCES_ALL_COLUMNS('TIENDAVIVA_ES_DEV.RAW.ORDERS', 'table'));
```

Returns `RECORD`, `PII`, `CUSTOMER_DATA`, `COLUMN`.

**No identifier in clear, and the protection is attached.**

```bash
uv run factory dbt TIENDAVIVA_ES test --select assert_customer_ref_is_pseudonymized assert_customer_ref_is_protected
```

**What no command can check.** Whether every source that carries personal data is marked
`personal_data: true`. The factory enforces it only for a source that feeds `customer_ref`.
A source with names or emails that are not mapped is tagged only if someone says so: go
through the data dictionary with the partner and confirm each source.

### Go-live checklist

- [ ] Line counts match for every sample file, and every file is `loaded`.
- [ ] Valid plus rejected equals records, and the difference to the standard model is explained.
- [ ] Totals match the partner's figures for the sample period.
- [ ] Every reject reason is understood and accepted by the partner.
- [ ] `factory dbt <PARTNER> test` passes; each warning is understood.
- [ ] `factory check-access <PARTNER>` says everything is as expected.
- [ ] Each source's `personal_data` flag was confirmed against the data dictionary.
- [ ] The partner has confirmed in writing that file names are never reused.
- [ ] `factory archive <PARTNER>` ran and left landing empty.

---

## 4. Failures, rollback and decommissioning

### 4.1 A file stays in landing

`factory load-status <PARTNER>` and the end of `factory archive <PARTNER>` list every file
still in landing with its state. Only `loaded` files are archived.

| State | Meaning | What to do |
|---|---|---|
| `loaded` | In RAW, complete, and landing still holds the same content | Nothing. The next `factory archive` moves it. |
| `not loaded` | No record in RAW and no load attempt | If it arrived less than a few minutes ago, wait. Otherwise the name does not match any `file_pattern`: fix the name with the partner, or the pattern (4.3). |
| `load failed` | A load was attempted and no record reached RAW | Read the copy history below. Ask for a corrected file under a new name. |
| `partially loaded` | The copy history reports errors, or fewer records in RAW than lines read | Read the copy history below. Ask for a corrected file under a new name, then move the partial one by hand (below). |
| `changed after load` | The same name was delivered again with other content. RAW has the first content; landing holds the second, which was never loaded | 4.2 |
| `load unverified` | Records are in RAW with the right checksum, but the copy history has expired (it lasts 14 days), so nothing proves the file loaded completely | Compare line counts and move it by hand (below). |

The copy history of a source:

```sql
SELECT file_name, status, row_count, row_parsed, error_count, first_error_message
FROM TABLE(DEMOSHOP_CO_DEV.INFORMATION_SCHEMA.COPY_HISTORY(
    TABLE_NAME => 'DEMOSHOP_CO_DEV.RAW.SALES',
    START_TIME => DATEADD(day, -13, CURRENT_TIMESTAMP())))
ORDER BY last_load_time;
```

A healthy file has `status` `Loaded`, `row_count` equal to `row_parsed` and `error_count` 0.

**`load unverified`: compare, then move by hand.** Count the lines of the file in landing and
compare with `RECORDS` for that file in `factory load-status`.

```bash
aws s3 cp "s3://$BUCKET/demoshop_co/landing/stores_20261001.csv" - | wc -l
```

If they are equal, the file loaded completely. Move it yourself; `factory archive` will not.

```bash
aws s3 mv "s3://$BUCKET/demoshop_co/landing/stores_20261001.csv" "s3://$BUCKET/demoshop_co/historical/stores_20261001.csv"
```

If they are not equal, treat it as `partially loaded`. The same move applies to a
`partially loaded` file once its correction has arrived under a new name and loaded. Records
that did load from the partial file stay in RAW; when the correction repeats them, the standard
model keeps one row per key.

Under normal operation `load unverified` should not appear: files are archived the day after
they load. It means the pipeline did not run for two weeks.

Not tested: what a pipe does with a malformed JSON line. Do not assume it; read the copy
history.

### 4.2 The partner re-sent a file under the same name

Snowpipe ignores it: no new load, same records in RAW. The file shows as
`changed after load` and is never archived, because what is in landing is not what was loaded.

```
In landing (processed files are moved to historical by `factory archive`):
FILE                STATE
sales_20261003.csv  changed after load
```

`factory archive` leaves it and still succeeds: the file was never on its list.

```
0 of 1 files in landing are loaded and will be archived
Still in landing:
FILE                STATE
sales_20261003.csv  changed after load
```

**Fix: deliver the correction again under a new name.** Ask the partner to send it, with a
suffix. In the rehearsal:

```bash
aws s3 cp sample_data/mercadolocal_br/sales_20261004_02.csv "s3://$BUCKET/demoshop_co/landing/sales_20261003_02.csv"
```

Confirm with `factory load-status` that the new file is `loaded`. Records with the same key
replace the earlier ones in the standard model on the next build.

Then remove the stale file, which now only blocks the listing. The bucket is versioned, so
both contents of that name remain for 90 days:

```bash
aws s3api list-object-versions --bucket "$BUCKET" --prefix demoshop_co/landing/sales_20261003.csv --query 'Versions[].[LastModified,Size,IsLatest]' --output text
```

```
2026-11-20T03:38:24+00:00	399	True
2026-11-20T03:37:03+00:00	4323	False
```

```bash
aws s3 rm "s3://$BUCKET/demoshop_co/landing/sales_20261003.csv"
```

Limit: the content that was loaded first reaches RAW but never `historical/` by this route.
It survives as an earlier version of the landing object for 90 days. Versioning was enabled on
19 November 2026; content overwritten before that is not recoverable from the bucket.

### 4.3 `factory deploy` stops because of differences

`deploy` only creates what is missing. If you change a source that is already deployed (a new
CSV column, another delimiter, another file pattern), it stops before changing anything, shows
both versions and prints the procedure. It exits with an error, so the Airflow run fails at its
first task.

```
pipe DEMOSHOP_CO_DEV.RAW.SALES_PIPE
    deployed:  COPY INTO DEMOSHOP_CO_DEV.RAW.SALES (...) 'user_role', $7, '_extra_field', $8), ...
    generated: COPY INTO DEMOSHOP_CO_DEV.RAW.SALES (...) 'user_role', $7, 'channel', $8, '_extra_field', $9), ...

The configuration no longer matches what is deployed. Nothing was changed: `deploy` never
replaces a pipe or a file format, because replacing a pipe drops its load history.
```

It does not fix it for you on purpose. A recreated pipe has no memory of what it loaded, and
would load again every file still in landing. The procedure, as rehearsed for a changed pipe:

```bash
uv run factory archive DEMOSHOP_CO
```

```sql
ALTER PIPE DEMOSHOP_CO_DEV.RAW.SALES_PIPE SET PIPE_EXECUTION_PAUSED = TRUE;
```

```bash
uv run factory load-status DEMOSHOP_CO     # the pipe shows PAUSED and 0 pending files
```

```sql
DROP PIPE DEMOSHOP_CO_DEV.RAW.SALES_PIPE;
```

```bash
uv run factory deploy DEMOSHOP_CO          # recreates the pipe; exits 0 when nothing differs
```

```sql
ALTER PIPE DEMOSHOP_CO_DEV.RAW.SALES_PIPE REFRESH;   -- loads files delivered while there was no pipe
```

Landing must be empty of loaded files before the pipe is dropped, which is what the first step
is for. For a file format the printed procedure uses `ALTER FILE FORMAT` instead of dropping
the pipe; that branch was not rehearsed.

If the difference is not a change you want, revert the partner file instead: `deploy` passes
again and nothing in Snowflake was touched.

### 4.4 Other failures

| Symptom | Cause | What to do |
|---|---|---|
| `factory validate` fails | The message names the file, the key and the rule | Fix the partner file. Nothing was deployed. |
| `the build failed: nothing was archived` | A dbt model or test failed | Read the dbt output above the message. Files stay in landing and are picked up by the next run. |
| `N of M files could not be archived` | A listed file could not be copied, confirmed or removed. Each file prints its reason. | Fix the cause and run `factory archive` again. A retry neither fails nor duplicates: files already moved are skipped. |
| A warning on `relationships_std_sales_transaction_store_id` | A sale of a store that is not in the store list | Expected when a store is listed a day late. If it persists, ask the partner. Report 3 shows such a store with an empty name. |
| `assert_incremental_matches_full_recompute` fails | The mapping changed and rows older than the lookback window were computed with the old one | 4.5, "A mapping that was wrong". |
| Many records rejected with the same reason | Usually the configuration: timestamp format, amount notation | 3.3, then fix the partner file and build again. |
| `PENDING_FILES` stays above 0, or a pipe is `PAUSED` | The pipe is paused or behind | If someone paused it, resume it: `ALTER PIPE MERCADOLOCAL_BR_DEV.RAW.SALES_PIPE SET PIPE_EXECUTION_PAUSED = FALSE;`. Otherwise wait and look again. |
| `set SNOWFLAKE_PRIVATE_KEY_FILE to the path of the private key` | The variable is not exported in this shell | Export it. The factory does not read `.env`. |
| `factory check-access` reports differences | A table was rebuilt outside the factory, or a grant was changed by hand | Run `uv run factory deploy <PARTNER>` and `uv run factory dbt <PARTNER> build`: both put grants, tags and policy back. Check again. |
| `check-access` looks wrong when you test by hand | Your own session has secondary roles on, which lend it access | Run `USE SECONDARY ROLES NONE;` first. The command already does. |

### 4.5 Rollback

RAW is append-only and is never rewritten by the pipeline. That is what makes rollback simple
for everything downstream of it: the standard model and the reports can always be rebuilt from
RAW.

**A partner file that is not merged yet.** Close the pull request. If it was deployed to DEV
for validation, see 4.6 or 4.7.

**A mapping that was wrong** (a field, a format, a time zone, the key). Revert or fix the
partner file and build again:

```bash
uv run factory dbt DEMOSHOP_CO build
```

The incremental model recomputes what was loaded in the last 24 hours (`--lookback-hours`
changes that). For rows older than the window, the test
`assert_incremental_matches_full_recompute` fails until the table is rebuilt from RAW:

```bash
uv run factory dbt MERCADOLOCAL_BR build --full-refresh
```

Know the cost before you run it: `--full-refresh` rebuilds the reports too, and a rebuilt
report holds only the snapshot of the day. Snapshots of earlier days are lost. This happened in
DEV on 19 November 2026. The standard model loses nothing. The masking policy, the tag and the
reader grants are put back in the same run, and `factory check-access` confirms it.

**A loader change that was wrong** (columns, delimiter, pattern). Revert the partner file and
follow 4.3 again. Records loaded while the wrong layout was deployed stay in RAW. There is no
command to delete records from RAW; they show up as rejected, or with values under the wrong
keys, until the partner sends those files again under new names.

**A bad delivery from the partner.** A corrected file under a new name. For the same key the
standard model keeps the latest version, or the last one received when the partner sends no
version. A record that should never have existed cannot be withdrawn by the partner today
other than by sending it again with a status that is not `COMPLETED`.

**A code change.** Revert the merge commit. Deployed loader objects do not change when code is
reverted, because `deploy` never replaces anything; the next build uses the reverted models.

### 4.6 Destructive reset of a partner in DEV

Dropping the database and deploying again is a **destructive reset**, not a rerun. Use it in
DEV to start a partner from zero. It is not how you repeat a demo: for that, deliver new files
under new names.

What it destroys: every record in RAW, the standard model, every report snapshot, the load
history of the pipes, and the pseudonymization key. A new key is generated on deploy, so
customer references computed afterwards do not match the earlier ones.

```sql
DROP DATABASE DEMOSHOP_CO_DEV;
```

```bash
uv run factory deploy DEMOSHOP_CO
```

The new pipes have no load history, so the files have to be delivered to landing again; files
already in `historical/` are not reloaded. No Terraform is needed: the queue of the bucket
notification does not change.

This drop-then-deploy sequence was run for the two sample partners on 19 November 2026, followed
by `terraform plan` with no changes. For `DEMOSHOP_CO` each command was run, but the deploy
came before the drop.

A dropped database can be restored for one day, the Time Travel retention of these databases:

```sql
UNDROP DATABASE DEMOSHOP_CO_DEV;
```

Rehearsed: the database came back with its data, and its pipes paused.

### 4.7 Decommissioning a partner

Rehearsed end to end with `DEMOSHOP_CO`. Before you start, agree with the partner and with
whoever owns the data what must be kept and for how long: after step 3 the data is gone from
Snowflake, and after step 4 from the bucket.

**1. Process what is left.**

```bash
uv run factory archive DEMOSHOP_CO
```

**2. Stop loading.** Pause every pipe of the partner, and pause its DAG in the Airflow UI.

```sql
ALTER PIPE DEMOSHOP_CO_DEV.RAW.STORES_PIPE SET PIPE_EXECUTION_PAUSED = TRUE;
ALTER PIPE DEMOSHOP_CO_DEV.RAW.SALES_PIPE SET PIPE_EXECUTION_PAUSED = TRUE;
```

**3. Drop the database.** This removes RAW, the standard model, the reports, the pipes, the
tag, the masking policy, the reader grants and the pseudonymization key. Without the key,
customer references in any copy made earlier can no longer be recomputed from an identifier.
The one-day restore of 4.6 applies.

```sql
DROP DATABASE DEMOSHOP_CO_DEV;
SHOW DATABASES LIKE '%_DEV';
```

**4. Remove the files.**

```bash
aws s3 rm "s3://$BUCKET/demoshop_co/" --recursive
```

The bucket is versioned: the removed files remain as earlier versions for 90 days and then
expire. If they must be gone now, for example for an erasure request, delete the versions:

```bash
PREFIX=demoshop_co/
aws s3api list-object-versions --bucket "$BUCKET" --prefix "$PREFIX" \
  --query '[Versions[].[Key,VersionId], DeleteMarkers[].[Key,VersionId]][]' --output text |
while read -r key version; do
  aws s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$version" > /dev/null
done
```

**5. Remove the configuration.** Delete `partners/<partner>.yml` in a pull request. Do this
last: the commands above need the file. After the merge the DAG disappears from Airflow.

```bash
uv run factory validate
```

Nothing else changes. The bucket, the AWS role, the notification, the warehouse and the reader
roles belong to the environment and are shared by the remaining partners.
