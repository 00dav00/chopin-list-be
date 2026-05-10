# Railway Deployment Notes — Near-Real-Time Lists

## Goal

Surface near-real-time list updates to the client without running a separate
realtime service. The current implementation does this with conditional GETs
(ETag / `If-None-Match` / 304) on `GET /lists/{list_id}` rather than
websockets — see [`conditional-get.md`](conditional-get.md) for the wire
contract.

## Topology

- Single `api` Railway service.
- Single MongoDB data source.
- No Redis, websocket service, or background worker required.

## Runtime Requirements

Standalone MongoDB. Change streams / replica-set mode are **not** required —
the polling path uses only `find_one` and `update_one`.

## Environment Contract

- `MONGO_URI`
- `MONGO_DB`
- `GOOGLE_CLIENT_ID`
- `CHOPIN_LIST_FE_URL`

## Rollout Checks

1. Deploy the API service.
2. `GET /lists/{list_id}` returns `200`, an `ETag: W/"…"` header, and
   `Cache-Control: private, no-cache`.
3. Repeat the GET with `If-None-Match: <captured-etag>` and confirm `304 Not
   Modified` with no body, the same ETag echoed, and the same Cache-Control.
4. Mutate the list (`POST /lists/{id}/items`) and repeat the conditional GET
   — confirm `200` with a different ETag.
5. No additional Railway services were introduced for realtime support.
