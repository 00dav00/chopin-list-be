# Conditional GET on `/lists/{list_id}`

`GET /lists/{list_id}` supports HTTP conditional requests so clients can poll
for changes cheaply.

## Response on a fresh GET

```
200 OK
ETag: W/"<integer-milliseconds>"
Cache-Control: private, no-cache
```

The ETag is weak (`W/"…"`) and is derived from `lists.updated_at` as integer
milliseconds since the epoch. It is deterministic from the persisted
timestamp alone — two replicas serving the same list emit the same ETag.

## Polling with `If-None-Match`

```
GET /lists/{list_id}
If-None-Match: W/"1715347200000"
```

- `304 Not Modified` (empty body) when the persisted `updated_at` still
  matches the ETag.
- `200 OK` with a fresh ETag when the list state has advanced.

A malformed `If-None-Match` header falls through to `200 OK` rather than
erroring.

## What invalidates the ETag

`lists.updated_at` is bumped by `mark_list_touched` (in `app/utils.py`)
after every item-write that mutates list contents:

- `POST /lists/{id}/items`
- `PATCH /items/{id}`
- `POST /items/{id}/toggle`
- `DELETE /items/{id}`
- `POST /lists/{id}/items/reorder`

Direct list updates (`PATCH /lists/{id}`, `complete`, `activate`) write
`updated_at` themselves and therefore also invalidate the ETag.

## Carve-out — create-from-template

`POST /templates/{id}/create-list` does **not** call `mark_list_touched`
after its bulk-insert. The list and its items are inserted with the same
`now` timestamp, so the first GET returns a stable ETag that 304s correctly
until the next item write. See `tests/test_templates.py
::test_create_list_from_template_skips_mark_list_touched` for the contract.

## Test coverage

- Helper unit tests: `tests/test_utils.py`
- Endpoint behavior (200/304/Cache-Control, malformed headers, ETag
  invalidation): `tests/test_lists.py`
- Per-mutation ETag bump tests: `tests/test_items.py`
- Template carve-out: `tests/test_templates.py`
