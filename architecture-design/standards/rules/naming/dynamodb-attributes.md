---
paths:
  - "**/drafts/**/*.json"
  - "stacks/*/domains/**/*.json"
  - "**/schemas/**"
---
# DynamoDB attribute names

Item attributes, not resource names. Table names live in `storage.md`.

| Kind | Form | Examples |
|---|---|---|
| scalar | `{concept}_{type}` | `pipeline_id`, `last_processed_at`, `watermark_col` |
| map | `{concept}` — singular, no suffix | `extract`, `silver`, `reconcile` |
| list | `{concept}_{type}s` — plural marks the list | `primary_key_cols`, `date_cols`, `tags` |
| boolean | bare predicate — no `is_`, no `has_` | `enabled`, `dry_run` |

## The scalar type suffix is closed

`_arn`, `_at`, `_col`, `_count`, `_date`, `_id`, `_mode`, `_name`, `_path`.

A scalar names its concept and then its type, so a reader knows what they will get before
they read the value. `last_processed_at` is a timestamp; `watermark_col` is a column name,
not the value in it.

## Why maps are singular and bare

A map is read *into*, not read *as* a value — `cfg["extract"]["mode"]`, never
`cfg["extract_map"]`. Adding a type suffix to a map implies it holds a single value.

A list is plural, and its suffix names the *element*: `primary_key_cols` is a list of
column names. `primary_keys_col` and `primary_key_col_list` are both wrong — the first
pluralises the wrong token, the second restates the container.

## Booleans carry no prefix

✅ `enabled`, `dry_run` ❌ `is_enabled`, `has_dry_run` — the type is already evident from
the value, and the prefix reads as noise in every conditional that uses it.

> This file is guidance only. Unlike resource names, attribute names are not
> mechanically enforced by the hook: judging arbitrary JSON keys produces false
> positives on legitimate drafts, and a hook that denies correct work is worse than one
> that misses. Enforcement is a follow-up once the false-positive rate is understood.
