# Bound the admin-notification abuse channel

**Status:** ready to ticket, not started
**Raised:** 2026-09-06
**Related:** `docs/plans/2026-09-06-001-feat-brevo-api-transport-plan.md` (the change that arms this), PR #5, commit `5bc83ae`
**Files it would touch:** `app/notifications.py`, `tests/test_notifications.py`

---

## Summary

Anyone with a Google account can trigger an admin notification email by signing in once. There is no rate limit, no cooldown, and no per-source deduplication. Nobody has noticed because the emails have never been delivered — Railway blocks outbound SMTP below the Pro plan, so every send has timed out. Once delivery works, the channel is live.

## Why this exists

`dispatch_new_user_notification` fires on every first-ever sign-in, from `app/auth.py`, before the request terminates in `403 Account pending approval`. The trigger requires a valid Google ID token for this application's client ID, so the actor is authenticated with Google — but not approved by us, and not otherwise gated. Creating accounts is cheap, and a single Google Workspace domain can provision hundreds of users at once.

Two things go wrong under a burst.

**The account's daily allowance is spent.** The Brevo free plan permits 300 sends per day, account-wide. A few hundred scripted sign-ins exhaust it. The genuine signup that the feature exists to surface is then either buried among the noise or suppressed entirely by the cap, and the account is exhibiting exactly the sending pattern that gets a transactional email account suspended.

**In-flight work grows without bound.** Each dispatch adds a task to a module-level set with no ceiling. A burst means unbounded concurrent HTTP clients, sockets and buffers inside the API process, each held for the duration of the request timeout. The notification module's central promise — that no failure in it changes the outcome of the request that triggered it — holds for one failing send. It has never been tested against a flood, and the flood consumes resources the sign-in path shares.

## What to do

Two bounds, both process-local, neither needing the retry or queue machinery that was deliberately kept out of the transport work.

- **Cap concurrent in-flight sends.** Past the ceiling, drop rather than queue, and log a count.
- **Cap the notification rate over a rolling window.** Beyond it, emit a single `suppressed N notifications` line rather than one line per attempt.

## Constraints that shaped this, and traps to avoid

**The admission check must not be able to break sign-in.** This is the trap that matters most. The module's guarantee comes from the task boundary — nothing awaits the task, so nothing inside it can reach the caller. But an admission check has to run *before* the task is created, inside `dispatch_new_user_notification`, which executes inline on the authentication path. Today the only `try` in that function wraps the task creation itself. Any new bookkeeping placed ahead of it sits outside every guard the module has: a bug in the window arithmetic, a type error on a counter, anything at all, propagates into `app/auth.py` and turns a first-time sign-in into a 500. The unit meant to protect that guarantee would be the first code placed where the guarantee does not apply. Either widen the existing guard to cover the admission check, or perform the check inside the task and drop there.

**A rolling-hour cap cannot bound a daily quota without dropping real signups.** The arithmetic does not work. Bounding 300 sends per day through an hourly cap requires roughly twelve per hour. At twelve per hour the control is indistinguishable from an outage during any ordinary launch day, onboarding push, or team rollout — and what it drops is precisely the genuine notification the feature exists to deliver. Set the cap anywhere useful and the daily quota is not actually protected. This needs deciding, not assuming. Two directions worth weighing:

- Weaken the goal to "cannot be exhausted by automated abuse" and pick a ceiling that tolerates a legitimate burst.
- Replace the rate cap with a daily budget derived from the 300 figure, plus coalescing — one digest naming N pending users instead of N separate emails. This bounds the quota without discarding the information.

Whichever is chosen, the ticket should state what happens to a suppressed genuine signup and how an administrator ever learns about it.

**Process-local bounds do not survive horizontal scaling.** Both ceilings would be in-memory, per-process counters. If the service ever runs more than one replica — plausible, since the eventual fix for the underlying SMTP block is a Railway plan upgrade, which could coincide with scaling — the effective bound multiplies by the replica count and the account-level quota is unprotected despite every instance honouring its own limit. A deploy or restart also resets the rolling window. Confirm the deployment is single-instance, or accept and record the caveat.

**The log may not be visible.** There is no logging configuration anywhere in the repository — no `basicConfig`, no `dictConfig` — so records propagate to a root logger that uvicorn never configures and fall to the last-resort handler at WARNING level. An `info`-level suppression count would probably not appear in production at all. If the suppression line is the only evidence a notification was dropped, it needs to be emitted at a level that actually lands.

## Acceptance criteria

- Dispatching past the in-flight ceiling drops the excess and logs a count, rather than growing the pending set.
- Dispatching past the rate cap suppresses further sends and logs once, not once per attempt.
- An ordinary single signup is unaffected by either bound.
- A failure inside the admission check itself does not raise out of `dispatch_new_user_notification`, and the authentication path still returns 403.
- Under a simulated burst, the number of sends is bounded, the pending set is bounded, and the sign-in path still responds promptly.
- The chosen numbers are recorded somewhere a reader can find them, with the reasoning.

## Open questions to resolve before starting

- What concrete values for the in-flight ceiling and the rate cap, and on what basis? The transport plan's convention is that tuning knobs are module constants rather than environment settings.
- What happens to a legitimate notification that gets suppressed, and how does an admin find out? A dropped notification means an admin never learns that person is waiting.
- Is the deployment guaranteed single-instance, or does the multi-replica caveat need to be accepted explicitly?
- Is coalescing into a digest the better shape than rate limiting, given the arithmetic above?
- Does the rolling window need to survive a process restart, or is process-local acceptable?

## How this was found

Surfaced during the confidence and document-review passes on the Brevo transport plan. It was briefly included there as an implementation unit, then removed: it is not what that change was scoped to do, and folding it in would have meant requirements written to justify work nobody asked for. It stands on its own, and this is the record.
