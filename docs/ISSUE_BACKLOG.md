# I2PChat Issue Backlog

This document collects issue-sized backlog items aligned with the roadmap in
[`ROADMAP.md`](ROADMAP.md). The text is written so each section can be copied
into a GitHub issue with minimal editing.

## Milestone: 0.6.5 - UX polish

### 1. Per-contact message drafts

**Description**

Store a separate message draft for each contact/address and restore it when the
user returns to the conversation.

**Acceptance criteria**

- Switching between conversations does not discard text in the input box.
- Each contact has its own independent draft state.
- Drafts survive reconnect flows where practical.
- Sending a message clears only the active conversation draft.

**Suggested labels**

`enhancement`, `ux`, `desktop`

### 2. Unread counters and indicators

**Description**

Add unread message state per conversation plus a global indicator for the main
window and/or tray.

**Acceptance criteria**

- Each conversation tracks unread count.
- New messages in an inactive conversation increment its unread count.
- Opening a conversation clears its unread count.
- The app exposes a global unread indicator in a visible place.

**Suggested labels**

`enhancement`, `ux`, `notifications`

### 3. Simplified connection and delivery status

**Description**

Present connection and delivery state in user-friendly language instead of only
showing low-level internal state.

**Acceptance criteria**

- The main UI exposes understandable states such as `Online`, `Sending`,
  `Will deliver later`, and `Disconnected`.
- More technical detail remains available without crowding the default UI.
- Status transitions remain stable and predictable during reconnects.

**Suggested labels**

`enhancement`, `ux`

### 4. Message context actions

**Description**

Add a context menu or equivalent quick actions for common message operations.

**Acceptance criteria**

- Text messages support `Copy text`.
- Messages support `Reply`.
- Attachments expose appropriate actions such as `Open` or `Copy path` where
  available.
- Behavior is consistent across incoming and outgoing messages where relevant.

**Suggested labels**

`enhancement`, `ux`

### 5. Notification preferences

**Description**

Expose notification controls that improve both privacy and day-to-day comfort.

**Acceptance criteria**

- Users can enable or disable notification sounds.
- Users can hide message text in notifications.
- A quiet mode is available.
- Notification behavior remains predictable when the window is focused or
  unfocused.

**Suggested labels**

`enhancement`, `notifications`, `privacy`

### 6. Attachment send UX improvements

**Description**

Improve progress and error visibility for file and image sends.

**Acceptance criteria**

- Users see understandable progress or state during attachment sends.
- Attachment failures are surfaced with clear error messaging.
- Failed sends do not look like silent disappearance.

**Suggested labels**

`enhancement`, `ux`, `attachments`

## Milestone: 0.7.0 - Contacts and conversations

*Status: issues 7–12 are implemented in **v0.7.0** (see [`releases/RELEASE_0.7.0.md`](releases/RELEASE_0.7.0.md)).*

*Implementation note (post–v0.7.0 tag): **Saved peers** list context menu — **Edit name & note…**, **Contact details…** (address, TOFU, remove pin), **Remove from saved peers…** (optional: encrypted history, pin, profile lock, BlindBox state file); core adds `clear_locked_peer()`, `contact_book` adds `remove_peer()`.*

### 7. Contacts sidebar

**Description**

Add a dedicated list of contacts/conversations to the main window.

**Acceptance criteria**

- Users can see saved conversations in a sidebar.
- The active conversation is visibly highlighted.
- Users can switch conversations directly from the list.
- Empty-state UX remains understandable.

**Suggested labels**

`feature`, `ux`, `desktop`

### 8. Local contact profiles

**Description**

Allow users to assign local names and short notes to contacts without hiding
their real I2P address.

**Acceptance criteria**

- A friendly display name can be stored for a contact.
- The name persists across restarts.
- A short note or tag can be stored.
- The real address remains visible somewhere in the contact UI.

**Suggested labels**

`feature`, `contacts`

### 9. Last active conversation restore

**Description**

Restore the last active conversation on startup when possible.

**Acceptance criteria**

- The last active conversation is reopened after restart when safe.
- Failures fall back to a safe default state.
- Drafts and unread state remain coherent after restore.

**Suggested labels**

`enhancement`, `ux`, `contacts`

### 10. Conversation preview rows

**Description**

Show a last-message preview and last-activity time inside the conversation
list.

**Acceptance criteria**

- Each conversation row shows a short preview of the last message.
- Last activity time is visible.
- Unread state is visible in the row.
- Rows degrade gracefully for attachments and non-text content.

**Suggested labels**

`enhancement`, `ux`, `contacts`

### 11. Search within current conversation

**Description**

Add local search over the current conversation history.

**Acceptance criteria**

- Users can search text in the current conversation.
- Search returns a list of matches.
- Selecting a result navigates to the matching message.
- Search does not break normal history loading.

**Suggested labels**

`feature`, `history`, `search`

### 12. Contact details / trust card MVP

**Description**

Create a basic contact details card with identity and trust-related fields.

**Acceptance criteria**

- Users can open a contact details view.
- The view shows address, fingerprint, and pinned/unpinned state.
- The information is understandable without hiding technical truth.
- The card does not block normal chat workflow.

**Suggested labels**

`feature`, `security`, `contacts`

## Milestone: 0.8.0 - Trust, delivery, offline clarity

### 13. Outgoing message queue UI

**Description**

Expose the outgoing queue and message lifecycle so offline delivery feels
intentional rather than opaque.

**Acceptance criteria**

- Outgoing messages can show a queued state.
- Users can distinguish local acceptance from actual delivery.
- Temporary failures do not erase queued state invisibly.
- UI wording does not imply delivery when only local queueing happened.

**Suggested labels**

`feature`, `delivery`, `ux`

### 14. Per-message delivery states

**Description**

Show delivery state directly on outgoing message bubbles.

**Acceptance criteria**

- Outgoing messages show a visible delivery state indicator.
- At minimum, states cover `sending`, `queued`, `delivered`, and `failed`.
- Additional detail is available via tooltip or a secondary view.
- State changes update cleanly without visual glitches.

**Suggested labels**

`feature`, `delivery`, `desktop`

### 15. Key change warning flow

**Description**

Provide an explicit UX flow when a contact's trusted key changes.

**Acceptance criteria**

- Users receive a clear warning when a peer key changes.
- The affected contact is identified clearly.
- Users can explicitly trust the new key or refuse the change.
- The app does not silently continue as if nothing happened.

**Suggested labels**

`feature`, `security`, `trust`

### 16. Improved trust UX and lock-to-peer clarity

**Description**

Make key pinning and lock-to-peer behavior easier to understand in the UI.

**Acceptance criteria**

- Users can see whether a contact is pinned or locked clearly.
- The meaning of lock-to-peer is explained in user-facing language.
- Resetting or forgetting a pinned key remains accessible.
- Trust state is not buried too deeply in menus.

**Suggested labels**

`enhancement`, `security`, `trust`, `ux`

### 17. BlindBox diagnostics screen

**Description**

Add a diagnostics screen that explains offline delivery availability and common
failure cases.

**Acceptance criteria**

- Users can see whether offline delivery is currently available.
- Failure reasons are described in human-readable language.
- The first version can be read-only.
- Diagnostics do not expose unnecessary technical noise by default.

**Suggested labels**

`feature`, `blindbox`, `diagnostics`

### 18. Retry failed messages

**Description**

Allow users to retry failed sends in a controlled and understandable way.

**Acceptance criteria**

- Failed messages expose a retry action.
- Retry does not create confusing duplicates without user intent.
- Message status updates correctly after retry attempts.
- Behavior remains consistent across live and offline flows where possible.

**Suggested labels**

`feature`, `delivery`, `ux`

## Milestone: 0.9.0 - Portability, privacy, hardening

*Status: issues **19–26** are shipped as the **v0.9.0** portability/privacy slice and included in the **v1.0.0** stable line ([`releases/RELEASE_0.9.0.md`](releases/RELEASE_0.9.0.md), [`releases/RELEASE_1.0.0.md`](releases/RELEASE_1.0.0.md)).*

### 19. Encrypted profile export

**Description**

Export a user profile into a portable, protected format for migration and
backup.

**Acceptance criteria**

- Profiles can be exported to a portable format.
- The exported form is encrypted or otherwise safely protected.
- Users are warned about storage and handling risks.
- Critical profile data is not silently omitted.

**Suggested labels**

`feature`, `security`, `backup`

### 20. Profile import and restore flow

**Description**

Import a previously exported profile into the application.

**Acceptance criteria**

- Exported profiles can be imported back successfully.
- Existing-profile conflicts are handled explicitly.
- Import failures are explained clearly.
- Imported profiles are usable without hidden repair steps.

**Suggested labels**

`feature`, `security`, `backup`

### 21. Encrypted history export/import

**Description**

Allow local chat history to be exported and restored in a protected format.

**Acceptance criteria**

- History can be exported separately from the profile.
- Export supports integrity verification.
- Import does not overwrite existing history without confirmation.
- The exported artifact is described clearly to the user.

**Suggested labels**

`feature`, `history`, `backup`, `security`

### 22. History retention controls

**Description**

Give users direct control over local history retention policy.

**Acceptance criteria**

- Users can set retention limits by age, amount, or similar practical policy.
- Retention behavior is predictable.
- Destructive operations require explicit confirmation.
- History cleanup does not destabilize the rest of the UI.

**Suggested labels**

`enhancement`, `history`, `privacy`

### 23. Privacy mode

**Description**

Add a local privacy mode for device-side protection.

**Acceptance criteria**

- Notification contents can be hidden.
- A quick privacy mode toggle exists.
- An optional local lock/unlock flow is supported if implemented.
- Privacy mode integrates cleanly with ordinary use.

**Suggested labels**

`feature`, `privacy`, `security`

### 24. Drag-and-drop attachments

**Description**

Support drag-and-drop for files and images in the chat window.

**Acceptance criteria**

- Files can be dropped into the chat window for send.
- Images can be dropped into the chat window for send.
- Unsupported content types produce clear feedback.
- Behavior aligns with existing attachment actions.

**Suggested labels**

`feature`, `attachments`, `ux`

### 25. Transfer retry and better media/file UX

**Description**

Improve retry and user feedback for file and media transfers.

**Acceptance criteria**

- Failed file sends can be retried.
- Transfer failures are surfaced with understandable reasons.
- Progress and preview behavior stays clear for normal media sends.
- Transfer state does not get stuck in confusing intermediate states.

**Suggested labels**

`enhancement`, `attachments`, `delivery`

### 26. Protocol and transfer hardening

**Description**

Strengthen protocol, transfer, and diagnostics coverage ahead of `1.0.0`.

**Acceptance criteria**

- Additional tests cover framing, damaged data paths, and transfer error cases.
- Logging and diagnostics improve for difficult network scenarios.
- CI reflects the versions the project actually intends to support.
- Reliability coverage is stronger than in the current baseline.

**Suggested labels**

`tech-debt`, `testing`, `reliability`

## Suggested dependency order

- Complete items 1-4 before deeper conversation-list work where practical.
- Build item 7 before items 10 and 11.
- Build item 12 before items 15 and 16.
- Build item 13 before items 14 and 18.
- Build item 19 before item 20.

