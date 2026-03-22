# Railway Live Updates Deployment Notes

## Goal

Ship v2 websocket live updates without introducing a separate realtime service.

## Topology

- Keep a single `api` Railway service.
- Keep MongoDB as the single data source.
- No Redis or background worker service is required for this phase.

## Runtime Requirements

MongoDB change streams are used as the live event source.
That requires MongoDB to run as a replica set (or equivalent managed mode that supports change streams).

## Environment Contract

The existing API environment variables remain unchanged:

- `MONGO_URI`
- `MONGO_DB`
- `GOOGLE_CLIENT_ID`
- `CHOPIN_LIST_FE_URL`

## Rollout Checks

1. Deploy API with websocket support enabled (same service and port).
2. Confirm MongoDB deployment supports change streams.
3. Open two sessions of the same list and verify `list.changed` events are received at `/v2/live/lists/{list_id}/ws`.
4. Verify no additional Railway services were introduced for realtime support.
