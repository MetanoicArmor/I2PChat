#!/usr/bin/env python3
"""
Sync roadmap milestones and backlog issues to GitHub.

Usage:

    GITHUB_TOKEN=ghp_xxx python3 scripts/sync_github_backlog.py

Optional:

    GITHUB_REPOSITORY=owner/repo GITHUB_TOKEN=ghp_xxx python3 scripts/sync_github_backlog.py

`GH_TOKEN` is accepted as an alias for `GITHUB_TOKEN` (same convention as GitHub CLI).
Do not put tokens in tracked files; pass them via the environment only.

The script is idempotent for the bundled milestone and issue titles:
- it creates missing labels;
- it creates missing milestones;
- it creates missing issues;
- it skips items that already exist by title.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "MetanoicArmor/I2PChat")
TOKEN = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()

if not TOKEN:
    print("error: GITHUB_TOKEN (or GH_TOKEN) is required", file=sys.stderr)
    sys.exit(1)

BASE_URL = f"https://api.github.com/repos/{REPOSITORY}"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "i2pchat-backlog-sync",
}

LABEL_COLORS = {
    "enhancement": "a2eeef",
    "ux": "1d76db",
    "desktop": "5319e7",
    "notifications": "c2e0c6",
    "privacy": "5319e7",
    "attachments": "fbca04",
    "feature": "0e8a16",
    "contacts": "bfd4f2",
    "history": "d4c5f9",
    "search": "f9d0c4",
    "security": "b60205",
    "delivery": "0052cc",
    "trust": "d93f0b",
    "blindbox": "7f52ff",
    "diagnostics": "006b75",
    "backup": "c5def5",
    "testing": "e4e669",
    "reliability": "0b7285",
    "tech-debt": "6c757d",
}

MILESTONES = [
    {
        "title": "0.6.5 - UX polish",
        "description": "Daily usability improvements with minimal architectural risk.",
    },
    {
        "title": "0.7.0 - Contacts and conversations",
        "description": "Contacts, conversation workflow, and local history navigation.",
    },
    {
        "title": "0.8.0 - Trust, delivery, offline clarity",
        "description": "Make trust model and offline delivery understandable in the UI.",
    },
    {
        "title": "0.9.0 - Portability, privacy, hardening",
        "description": "Backup, portability, privacy controls, and reliability work ahead of 1.0.",
    },
]

ISSUES = [
    {
        "title": "Per-contact message drafts",
        "milestone": "0.6.5 - UX polish",
        "labels": ["enhancement", "ux", "desktop"],
        "body": """## Description
Store a separate message draft for each contact/address and restore it when the user returns to the conversation.

## Acceptance criteria
- Switching between conversations does not discard text in the input box.
- Each contact has its own independent draft state.
- Drafts survive reconnect flows where practical.
- Sending a message clears only the active conversation draft.
""",
    },
    {
        "title": "Unread counters and indicators",
        "milestone": "0.6.5 - UX polish",
        "labels": ["enhancement", "ux", "notifications"],
        "body": """## Description
Add unread message state per conversation plus a global indicator for the main window and/or tray.

## Acceptance criteria
- Each conversation tracks unread count.
- New messages in an inactive conversation increment its unread count.
- Opening a conversation clears its unread count.
- The app exposes a global unread indicator in a visible place.
""",
    },
    {
        "title": "Simplified connection and delivery status",
        "milestone": "0.6.5 - UX polish",
        "labels": ["enhancement", "ux"],
        "body": """## Description
Present connection and delivery state in user-friendly language instead of only showing low-level internal state.

## Acceptance criteria
- The main UI exposes understandable states such as `Online`, `Sending`, `Will deliver later`, and `Disconnected`.
- More technical detail remains available without crowding the default UI.
- Status transitions remain stable and predictable during reconnects.
""",
    },
    {
        "title": "Message context actions",
        "milestone": "0.6.5 - UX polish",
        "labels": ["enhancement", "ux"],
        "body": """## Description
Add a context menu or equivalent quick actions for common message operations.

## Acceptance criteria
- Text messages support `Copy text`.
- Messages support `Reply`.
- Attachments expose appropriate actions such as `Open` or `Copy path` where available.
- Behavior is consistent across incoming and outgoing messages where relevant.
""",
    },
    {
        "title": "Notification preferences",
        "milestone": "0.6.5 - UX polish",
        "labels": ["enhancement", "notifications", "privacy"],
        "body": """## Description
Expose notification controls that improve both privacy and day-to-day comfort.

## Acceptance criteria
- Users can enable or disable notification sounds.
- Users can hide message text in notifications.
- A quiet mode is available.
- Notification behavior remains predictable when the window is focused or unfocused.
""",
    },
    {
        "title": "Attachment send UX improvements",
        "milestone": "0.6.5 - UX polish",
        "labels": ["enhancement", "ux", "attachments"],
        "body": """## Description
Improve progress and error visibility for file and image sends.

## Acceptance criteria
- Users see understandable progress or state during attachment sends.
- Attachment failures are surfaced with clear error messaging.
- Failed sends do not look like silent disappearance.
""",
    },
    {
        "title": "Contacts sidebar",
        "milestone": "0.7.0 - Contacts and conversations",
        "labels": ["feature", "ux", "desktop"],
        "body": """## Description
Add a dedicated list of contacts/conversations to the main window.

## Acceptance criteria
- Users can see saved conversations in a sidebar.
- The active conversation is visibly highlighted.
- Users can switch conversations directly from the list.
- Empty-state UX remains understandable.
""",
    },
    {
        "title": "Local contact profiles",
        "milestone": "0.7.0 - Contacts and conversations",
        "labels": ["feature", "contacts"],
        "body": """## Description
Allow users to assign local names and short notes to contacts without hiding their real I2P address.

## Acceptance criteria
- A friendly display name can be stored for a contact.
- The name persists across restarts.
- A short note or tag can be stored.
- The real address remains visible somewhere in the contact UI.
""",
    },
    {
        "title": "Last active conversation restore",
        "milestone": "0.7.0 - Contacts and conversations",
        "labels": ["enhancement", "ux", "contacts"],
        "body": """## Description
Restore the last active conversation on startup when possible.

## Acceptance criteria
- The last active conversation is reopened after restart when safe.
- Failures fall back to a safe default state.
- Drafts and unread state remain coherent after restore.
""",
    },
    {
        "title": "Conversation preview rows",
        "milestone": "0.7.0 - Contacts and conversations",
        "labels": ["enhancement", "ux", "contacts"],
        "body": """## Description
Show a last-message preview and last-activity time inside the conversation list.

## Acceptance criteria
- Each conversation row shows a short preview of the last message.
- Last activity time is visible.
- Unread state is visible in the row.
- Rows degrade gracefully for attachments and non-text content.
""",
    },
    {
        "title": "Search within current conversation",
        "milestone": "0.7.0 - Contacts and conversations",
        "labels": ["feature", "history", "search"],
        "body": """## Description
Add local search over the current conversation history.

## Acceptance criteria
- Users can search text in the current conversation.
- Search returns a list of matches.
- Selecting a result navigates to the matching message.
- Search does not break normal history loading.
""",
    },
    {
        "title": "Contact details / trust card MVP",
        "milestone": "0.7.0 - Contacts and conversations",
        "labels": ["feature", "security", "contacts"],
        "body": """## Description
Create a basic contact details card with identity and trust-related fields.

## Acceptance criteria
- Users can open a contact details view.
- The view shows address, fingerprint, and pinned/unpinned state.
- The information is understandable without hiding technical truth.
- The card does not block normal chat workflow.
""",
    },
    {
        "title": "Outgoing message queue UI",
        "milestone": "0.8.0 - Trust, delivery, offline clarity",
        "labels": ["feature", "delivery", "ux"],
        "body": """## Description
Expose the outgoing queue and message lifecycle so offline delivery feels intentional rather than opaque.

## Acceptance criteria
- Outgoing messages can show a queued state.
- Users can distinguish local acceptance from actual delivery.
- Temporary failures do not erase queued state invisibly.
- UI wording does not imply delivery when only local queueing happened.
""",
    },
    {
        "title": "Per-message delivery states",
        "milestone": "0.8.0 - Trust, delivery, offline clarity",
        "labels": ["feature", "delivery", "desktop"],
        "body": """## Description
Show delivery state directly on outgoing message bubbles.

## Acceptance criteria
- Outgoing messages show a visible delivery state indicator.
- At minimum, states cover `sending`, `queued`, `delivered`, and `failed`.
- Additional detail is available via tooltip or a secondary view.
- State changes update cleanly without visual glitches.
""",
    },
    {
        "title": "Key change warning flow",
        "milestone": "0.8.0 - Trust, delivery, offline clarity",
        "labels": ["feature", "security", "trust"],
        "body": """## Description
Provide an explicit UX flow when a contact's trusted key changes.

## Acceptance criteria
- Users receive a clear warning when a peer key changes.
- The affected contact is identified clearly.
- Users can explicitly trust the new key or refuse the change.
- The app does not silently continue as if nothing happened.
""",
    },
    {
        "title": "Improved trust UX and lock-to-peer clarity",
        "milestone": "0.8.0 - Trust, delivery, offline clarity",
        "labels": ["enhancement", "security", "trust", "ux"],
        "body": """## Description
Make key pinning and lock-to-peer behavior easier to understand in the UI.

## Acceptance criteria
- Users can see whether a contact is pinned or locked clearly.
- The meaning of lock-to-peer is explained in user-facing language.
- Resetting or forgetting a pinned key remains accessible.
- Trust state is not buried too deeply in menus.
""",
    },
    {
        "title": "BlindBox diagnostics screen",
        "milestone": "0.8.0 - Trust, delivery, offline clarity",
        "labels": ["feature", "blindbox", "diagnostics"],
        "body": """## Description
Add a diagnostics screen that explains offline delivery availability and common failure cases.

## Acceptance criteria
- Users can see whether offline delivery is currently available.
- Failure reasons are described in human-readable language.
- The first version can be read-only.
- Diagnostics do not expose unnecessary technical noise by default.
""",
    },
    {
        "title": "Retry failed messages",
        "milestone": "0.8.0 - Trust, delivery, offline clarity",
        "labels": ["feature", "delivery", "ux"],
        "body": """## Description
Allow users to retry failed sends in a controlled and understandable way.

## Acceptance criteria
- Failed messages expose a retry action.
- Retry does not create confusing duplicates without user intent.
- Message status updates correctly after retry attempts.
- Behavior remains consistent across live and offline flows where possible.
""",
    },
    {
        "title": "Encrypted profile export",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["feature", "security", "backup"],
        "body": """## Description
Export a user profile into a portable, protected format for migration and backup.

## Acceptance criteria
- Profiles can be exported to a portable format.
- The exported form is encrypted or otherwise safely protected.
- Users are warned about storage and handling risks.
- Critical profile data is not silently omitted.
""",
    },
    {
        "title": "Profile import and restore flow",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["feature", "security", "backup"],
        "body": """## Description
Import a previously exported profile into the application.

## Acceptance criteria
- Exported profiles can be imported back successfully.
- Existing-profile conflicts are handled explicitly.
- Import failures are explained clearly.
- Imported profiles are usable without hidden repair steps.
""",
    },
    {
        "title": "Encrypted history export/import",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["feature", "history", "backup", "security"],
        "body": """## Description
Allow local chat history to be exported and restored in a protected format.

## Acceptance criteria
- History can be exported separately from the profile.
- Export supports integrity verification.
- Import does not overwrite existing history without confirmation.
- The exported artifact is described clearly to the user.
""",
    },
    {
        "title": "History retention controls",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["enhancement", "history", "privacy"],
        "body": """## Description
Give users direct control over local history retention policy.

## Acceptance criteria
- Users can set retention limits by age, amount, or similar practical policy.
- Retention behavior is predictable.
- Destructive operations require explicit confirmation.
- History cleanup does not destabilize the rest of the UI.
""",
    },
    {
        "title": "Privacy mode",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["feature", "privacy", "security"],
        "body": """## Description
Add a local privacy mode for device-side protection.

## Acceptance criteria
- Notification contents can be hidden.
- A quick privacy mode toggle exists.
- An optional local lock/unlock flow is supported if implemented.
- Privacy mode integrates cleanly with ordinary use.
""",
    },
    {
        "title": "Drag-and-drop attachments",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["feature", "attachments", "ux"],
        "body": """## Description
Support drag-and-drop for files and images in the chat window.

## Acceptance criteria
- Files can be dropped into the chat window for send.
- Images can be dropped into the chat window for send.
- Unsupported content types produce clear feedback.
- Behavior aligns with existing attachment actions.
""",
    },
    {
        "title": "Transfer retry and better media/file UX",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["enhancement", "attachments", "delivery"],
        "body": """## Description
Improve retry and user feedback for file and media transfers.

## Acceptance criteria
- Failed file sends can be retried.
- Transfer failures are surfaced with understandable reasons.
- Progress and preview behavior stays clear for normal media sends.
- Transfer state does not get stuck in confusing intermediate states.
""",
    },
    {
        "title": "Protocol and transfer hardening",
        "milestone": "0.9.0 - Portability, privacy, hardening",
        "labels": ["tech-debt", "testing", "reliability"],
        "body": """## Description
Strengthen protocol, transfer, and diagnostics coverage ahead of `1.0.0`.

## Acceptance criteria
- Additional tests cover framing, damaged data paths, and transfer error cases.
- Logging and diagnostics improve for difficult network scenarios.
- CI reflects the versions the project actually intends to support.
- Reliability coverage is stronger than in the current baseline.
""",
    },
]


def api_request(method: str, path: str, data: dict | None = None) -> object:
    payload = None
    headers = dict(HEADERS)
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=payload,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8") or "null"
    return json.loads(raw)


def api_get_list_paginated(path: str) -> list:
    """GET a JSON array endpoint with GitHub pagination (100 items per page)."""
    base = path.rstrip("&")
    connector = "&" if "?" in base else "?"
    all_rows: list = []
    page = 1
    while True:
        chunk = api_request("GET", f"{base}{connector}per_page=100&page={page}")
        if not isinstance(chunk, list):
            raise TypeError(f"expected list from {path}, got {type(chunk)}")
        all_rows.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return all_rows


def ensure_labels() -> None:
    existing = {item["name"] for item in api_get_list_paginated("/labels")}
    for name, color in LABEL_COLORS.items():
        if name in existing:
            print(f"label exists: {name}")
            continue
        api_request("POST", "/labels", {"name": name, "color": color})
        print(f"label created: {name}")


def ensure_milestones() -> dict[str, int]:
    milestones = {
        item["title"]: int(item["number"])
        for item in api_get_list_paginated("/milestones?state=all")
    }
    for milestone in MILESTONES:
        if milestone["title"] in milestones:
            print(f"milestone exists: {milestone['title']}")
            continue
        created = api_request("POST", "/milestones", milestone)
        milestones[milestone["title"]] = int(created["number"])
        print(f"milestone created: {milestone['title']}")
    return milestones


def ensure_issues(milestones: dict[str, int]) -> None:
    existing = {
        item["title"]
        for item in api_get_list_paginated("/issues?state=all")
        if "pull_request" not in item
    }
    for issue in ISSUES:
        if issue["title"] in existing:
            print(f"issue exists: {issue['title']}")
            continue
        payload = {
            "title": issue["title"],
            "body": issue["body"],
            "labels": issue["labels"],
            "milestone": milestones[issue["milestone"]],
        }
        created = api_request("POST", "/issues", payload)
        print(f"issue created: #{created['number']} {issue['title']}")


def main() -> int:
    try:
        ensure_labels()
        milestones = ensure_milestones()
        ensure_issues(milestones)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"GitHub API error: HTTP {exc.code}", file=sys.stderr)
        print(body, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
