---
date: 2026-10-18
topic: group-items-by-kind
notion: https://app.notion.com/p/Group-and-sort-items-using-AI-3a87386abcc380d4a851eac528089e50
---

# Group Items by Kind

## Summary

A "Group by kind" button on shopping lists and templates. One tap asks a language model to put each item, bought or not, into one of a set of admin-managed groups, then rewrites the list order to follow the group order, with "Other" last. Admins manage the groups in the app. When items land in "Other", the shopper sees which ones and chooses whether to send them to admins. Sent items are emailed to admins and collected in an admin screen, where an admin can turn any of them into a new group.

---

## Problem Frame

Shopping lists are a flat, manually ordered sequence. In a store a shopper passes one kind of product at a time, so a list in entry order forces repeated scanning and walking back and forth across the shop floor.

Today shoppers fix this by dragging items into a sensible order before leaving for the store. That costs time on every trip. On phones it doesn't work at all: drag reordering is broken on touch screens (separate open ticket, "Active-list reorder is non-functional on touch/mobile"). Items carry no notion of what kind of product they are, so nothing in the app can help.

Ordering groups to match one particular store's layout is a separate, later capability that builds on this one.

---

## Actors

- A1. Shopper: anyone who can edit a list or template. Taps the button, decides whether to send unrecognised items to admins, and drags afterwards if needed.
- A2. Admin: a user flagged as admin. Manages the groups, receives the "unmatched items" emails and works through the unmatched-items screen.
- A3. Language model: picks one group per item, choosing only from the admin-managed groups or "Other".

---

## Key Flows

- F1. Shopper groups a list
  - **Trigger:** Shopper taps "Group by kind" on a list or template that has at least one item.
  - **Actors:** A1, A3
  - **Steps:** The current groups and all item names, bought ones included, are sent to the model. The model returns a group per item. All items are put in one order by group, with "Other" last. Bought items still show in their own section at the bottom, so both sections appear in group order. Each item's group is saved. If any items landed in "Other", the shopper sees a message listing them and asking whether to send them to the admins for checking. Only if the shopper confirms are they recorded for admins and emailed.
  - **Outcome:** The list shows the new order to everyone viewing it. The shopper can drag freely afterwards. Unrecognised items reach admins only with the shopper's consent.
  - **Failure path:** If the model call fails, the list is unchanged, the shopper sees a short "couldn't group right now, try again" message, and nothing is recorded or emailed.
  - **Covered by:** R1–R9, R15, R17, R20, R21

- F2. Admin turns an unmatched item into a group
  - **Trigger:** Admin opens the unmatched-items screen (for example, after an email).
  - **Actors:** A2
  - **Steps:** Admin sees each unmatched name once, with how many times it was seen and when it was last seen. Admin taps a name. A new-group form opens with the name pre-filled. Admin edits the name if needed, then saves. The new group goes at the end of the order (just before "Other"), and the admin can move it afterwards.
  - **Outcome:** The new group exists, and the name leaves the unmatched list. Existing lists don't change until a shopper taps the button again.
  - **Escape path:** Admin dismisses the name without creating a group.
  - **Covered by:** R13, R14

---

## Requirements

**Grouping (shopper)**
- R1. Lists and templates both have a "Group by kind" action. Grouping never happens automatically.
- R2. On tap, every item, bought or not, is sent to the model and given exactly one group from the current admin-managed set, or "Other". Nothing is remembered between taps: every tap asks about every item again.
- R3. All items are reordered so the groups appear in the admin-defined order, with "Other" last. Order within a group is whatever the model returns.
- R4. Bought items are grouped too, in one order shared with unbought items. They still show in their own section below the unbought items, so that section also follows group order. An item unticked later goes back among its group, not to the end of the unbought items.
- R5. The result is a plain reordered list: no group headings, labels or tags are shown to shoppers.
- R6. After grouping, the shopper can drag items freely, and that manual order sticks until the next tap. Items added later go to the end of the list, as they do today.
- R7. Each item's assigned group is saved on the item (for the future store-layout ticket) but not shown, and is overwritten on the next tap.
- R8. Changes made to the list while the model is working are kept, not overwritten. An item added during the wait goes to the end of the list (so, while unbought, at the end of the unbought items). An item deleted during the wait is simply left out. An item marked bought or unticked during the wait keeps that state and keeps its group's place, so it shows in its new section in group order. An item renamed during the wait keeps its new name and is placed by the group chosen for its old name.
- R9. Other viewers of the same list see the new order on their next routine check for changes, exactly as they do after a manual drag-reorder. Grouping saves its order the same way a drag-reorder does, so it marks the list as changed.

**Groups (admin)**
- R10. Groups and their order are stored in the database, not in a file. A one-time console command (alongside the existing admin commands) pre-fills them with a basic supermarket set (for example: fruit, vegetables, vegetarian meat, meat, fish, bakery, dairy, frozen, dry goods, drinks, household). It refuses to run if any groups already exist, so running it twice is harmless.
- R11. Admins can add, rename, delete and reorder groups in an admin screen in the app. Changes take effect on the next tap, with no deploy.
- R12. "Other" is built in: always last, and can't be renamed, reordered or deleted.

**Unmatched items (admin)**
- R13. Item names that the shopper chose to send (R15) are recorded. An admin screen lists each name once, with how many times it was sent and when it was last sent.
- R14. From that screen an admin can tap a name to create a new group pre-filled with that name, and edit the name. The new group is added at the end of the order, just before "Other". The name then leaves the unmatched list. An admin can also dismiss a name without creating a group.
- R15. When a tap leaves at least one item in "Other" (bought or not), the shopper who tapped (and only that shopper) sees a message listing those items and asking whether to send them to the admins for checking. Sending is all-or-nothing for that tap. If the shopper confirms, the names are recorded (R13) and all admins get an email listing them. If the shopper declines or closes the message, nothing is recorded or emailed. The grouping itself is already applied either way.
- R16. What admins receive, in the email and on the screen, is the item names only: not who sent them or which list they came from.

**Failure behaviour**
- R17. If the model call fails, the list stays exactly as it was, nothing is partly reordered, nothing is recorded, no email is sent, and the shopper sees a short retry message.
- R18. If the admin email fails, the failure is skipped without retrying, and the grouping still succeeds.
- R19. With no items at all, the action does nothing (or is disabled). No model call, recording or email happens.
- R20. The model is called while the shopper waits. The button shows a "thinking" spinner until the result arrives or the call fails, and the shopper can't start a second grouping on the same list while one is running.
- R21. If the model's answer leaves out an item, names a group that doesn't exist, or is otherwise unusable for an item, that item goes to "Other" (and is offered to the shopper for sending like any other unmatched item). Only a failed call or a completely unusable answer counts as a failure under R17.

---

## Acceptance Examples

- AE1. **Covers R2, R3, R4.** Given a list with unbought "milk", "apples", "birthday card", "chicken" and bought "pears", when the shopper taps the button, the unbought items show as apples, chicken, milk, birthday card (fruit, meat, dairy, Other), and "pears" shows in the bought section. The saved order is apples, pears, chicken, milk, birthday card.
- AE2. **Covers R6.** Given a grouped list, when the shopper adds "yoghurt", it appears at the end of the unbought items. When they tap the button again, it moves in with dairy.
- AE3. **Covers R13, R15.** Given "birthday card" lands in "Other" on two separate taps and the shopper chooses to send it both times, admins get two emails, and the unmatched-items screen shows "birthday card" once, sent 2 times.
- AE4. **Covers R14, R2.** Given an admin creates a "Cards & gifts" group from "birthday card", existing lists don't change. The next time a shopper taps the button, the model can place "birthday card" in "Cards & gifts".
- AE5. **Covers R17.** Given the model is unavailable, when the shopper taps the button, the list order doesn't change, they see a retry message, and admins get no email.
- AE6. **Covers R21.** Given the model answers for 9 of 10 items and leaves out "tahini", the 9 are grouped normally, and "tahini" goes to "Other" and is listed in the message offering to send it to admins.
- AE7. **Covers R11.** Given an admin moves "dairy" above "fruit", the next grouping on any list puts dairy items before fruit items. Lists grouped earlier keep their old order until tapped again.
- AE8. **Covers R15.** Given "pregnancy test" lands in "Other", the shopper sees it listed in the message and declines. The list is still grouped, and admins get no email and see nothing on their screen.
- AE9. **Covers R8.** Given shopper A taps the button on a shared list, and while the spinner shows shopper B adds "eggs" and ticks "milk" as bought, then when grouping finishes "eggs" is at the end of the unbought items and "milk" is in the bought section, in dairy's place.
- AE10. **Covers R4.** Given a grouped list where bought "pears" was grouped with fruit, when the shopper unticks "pears", it shows next to "apples" among the unbought items, not at the end.

---

## Success Criteria

- A shopper can get a list into a sensible store-walk order with one tap plus at most a couple of drags, instead of dragging every item before each trip, including on a phone.
- Admins can see which items the groups fail to cover and fix the gap from the app without a deploy.
- Planning can proceed without inventing shopper-visible behaviour, admin abilities or failure behaviour.

---

## Scope Boundaries

- Ordering groups to match a specific store's layout (separate follow-up ticket).
- Shoppers seeing or editing an item's group.
- Grouping items automatically when they are added.
- Remembering groups by item name, or keyword shortcuts that skip the model.
- Adding an unmatched name to an *existing* group (only "create a new group" is in scope).
- Choosing individual items to send to admins (sending is all-or-nothing per tap).
- Fixing touch drag on mobile (its own ticket).
- Rate limits, digests or deduplication for the admin email.

---

## Key Decisions

- Button, not automatic: shoppers stay in control of their manual order, and adding items stays as fast as it is now.
- Ask the model on every tap: chosen for simplicity over remembering answers by name. Admin changes to groups take effect straight away, and the cost is one model call per tap.
- Groups in the database with an admin screen, not a file: admins can reorder and extend groups without a deploy. The store-layout ticket can later replace the single global order per store.
- "Other" is fixed and last: gives unplaceable items and notes a predictable home.
- The shopper chooses whether unrecognised items go to admins: "Other" is where personal items end up (medicines, notes to self), so nothing leaves a shopper's list for admins without their consent, and admins see names only. When sent, the email gives prompt notice and the screen is where admins act.
- Edits made during the wait win over the grouping: on a shared list, someone else's change must never be lost because a grouping finished after it.
- Bought items are grouped in the same single order as unbought items (decided 2026-10-19). The page already shows bought items in their own section, so the result looks the same after a tap; the gain is that an unticked item returns to its group instead of the end of the list.
- Within-group order is left to the model: shoppers accept a couple of drags, so it doesn't need a rule.
- Mistral Large 3 on AWS Bedrock as the model: about $0.001 per tap for a 50-item list (third-party pricing, roughly $0.50 / $1.50 per million tokens in and out in US East; to be confirmed against AWS's pricing page for the chosen region). Chosen as a mid-priced option that is reliable enough for a closed choice from a short list.
- The model is called during the request, with a spinner: simpler than background work, and a list is small enough for the shopper to wait.
- A one-time console command fills in the default groups, rather than filling at startup or adding a migration tool. The backend has no migration tool, and filling "when empty" at startup would silently bring the defaults back if an admin deleted every group.
- Bad model answers degrade item by item to "Other" instead of failing the whole grouping, so admins still see the gaps.

---

## Dependencies / Assumptions

- The backend has no language-model dependency today (`requirements.txt` has none). Adding AWS Bedrock access, plus credentials in each environment, is new.
- Admin email reuses the existing admin-notification path in `app/notifications.py`, which sends to every user flagged `admin` through Brevo.
- Accepted risk: emails sent on the shopper's confirmation share Brevo's free-plan allowance of 300 emails a day with sign-up alerts. See `docs/plans/2026-09-06-002-bound-notification-abuse-channel.md`. Asking the shopper first lowers the volume, but heavy use could still crowd out sign-up notifications.
- All item names, bought ones and personal ones included, are sent to AWS Bedrock on every tap.
- Reordering reuses the existing list and template reorder behaviour, which expects the complete set of item ids. R8 means the ids sent must reflect the list as it is when the model answers, not as it was when the tap started.

---

## Outstanding Questions

### Deferred to Planning

- [Affects R2, R20][Needs research] Confirm Mistral Large 3 is available in the deployment's AWS region, measure the typical wait for a 20–50 item list, and pick a timeout after which the call counts as failed (R17).
