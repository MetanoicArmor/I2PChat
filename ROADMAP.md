# I2PChat Roadmap

Current repository version: `1.0.0`

This roadmap is organized by product milestones rather than calendar time.
The main goal is to improve daily usability first, then strengthen dialog
workflow, trust visibility, offline delivery clarity, and finally portability
and long-term retention before `1.0.0`.

## Guiding principles

- Keep protocol and transport changes minimal unless they clearly unlock user value.
- Prefer UX improvements that expose existing capabilities before adding new complexity.
- Preserve the privacy-first character of the project.
- Defer high-risk features such as group chat and multi-device sync until after `1.0.0`.

## 0.6.5 - UX polish

Goal: make the application easier to use every day without major architectural
changes.

Planned focus areas:

- Per-contact message drafts
- Unread counters and tray/window indicators
- Simplified connection and delivery status text
- Message context actions (`Copy`, `Reply`, attachment actions where applicable)
- Notification preferences (sound, quiet mode, hide message text)
- Better attachment send feedback and clearer transfer errors

Release outcome:

- users do not lose text while switching context;
- users can see which chats need attention;
- connection and delivery behavior becomes easier to understand;
- common message interactions feel more natural.

## 0.7.0 - Contacts and conversations

Goal: evolve the app from a single-peer session client into a conversation-based
messenger.

Planned focus areas:

- Contacts sidebar / conversation list
- Local names and notes for contacts
- Last active conversation restore
- Conversation previews (last message, last activity, unread state)
- Search within the current conversation history
- Basic contact details / trust card MVP

Release outcome:

- users can move between saved contacts quickly;
- message history becomes part of normal navigation;
- contact identity is easier to manage without losing the real I2P address.

## 0.8.0 - Trust, delivery, offline clarity

Goal: make the product's strongest differentiators understandable in the UI.

Planned focus areas:

- Outgoing message queue UI
- Per-message delivery states (`sending`, `queued`, `delivered`, `failed`)
- Key change warning flow
- Clearer trust UX for pinning and lock-to-peer
- BlindBox diagnostics screen
- Retry for failed sends where behavior is safe and predictable

Release outcome:

- users can tell whether a message was sent live or queued offline;
- trust changes are surfaced explicitly instead of being hidden in low-level state;
- offline delivery failures become diagnosable.

## 0.9.0 - Portability, privacy, hardening

Goal: make the application ready for longer-term real-world use.

Planned focus areas:

- Encrypted profile export
- Profile import / restore flow
- Encrypted history export and import
- History retention controls
- Privacy mode (notification hiding, optional local lock flow)
- Drag-and-drop attachments
- Better transfer retry and media/file UX
- Protocol and transfer hardening in tests and diagnostics

Release outcome:

- users can export and restore profile/state backups through encrypted bundles;
- local privacy controls become stronger;
- reliability improves ahead of `1.0.0`.

## 1.0.0 - Stable niche release

Goal: ship a stable, privacy-focused release with a clear conversation model and
predictable delivery/trust UX.

Minimum expected state:

- convenient dialog and contact workflow;
- searchable local history;
- understandable delivery states;
- explicit trust/key-change UX;
- usable offline delivery diagnostics;
- practical backup and restore paths;
- better reliability coverage for protocol and transfers.

Release outcome:

- profile backups and history backups can be exported and restored in encrypted form;
- local history retention is configurable by count and age;
- privacy mode is available as a quick local safeguard;
- drag-and-drop attachments match existing send actions;
- pre-1.0 reliability coverage is strong enough for a stable niche release.

## Post-1.0 candidates

These are intentionally left outside the pre-1.0 scope:

- group chats;
- real-time multi-device sync;
- plugin or scripting systems;
- broader automation surfaces beyond local preferences.

## Tracking

Detailed issue-sized backlog items for the milestones above are collected in
[`ISSUE_BACKLOG.md`](ISSUE_BACKLOG.md).

Per-version release notes live under [`docs/releases/README.md`](docs/releases/README.md).
