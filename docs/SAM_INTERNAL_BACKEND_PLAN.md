# Internal SAM Backend Plan

## Status (post-migration)

The **public SAM runtime path** is implemented in **`i2pchat.sam`**. Normal application
bootstrap does **not** require `vendor/i2plib` or `sys.path` injection for it.

**Follow-up work** (separate from this document’s original rollout):

- scrub remaining **comments/docs** that still describe vendored `i2plib` as the live transport;
- extend **tests** (malformed SAM replies, timeout cleanup, session failure paths);
- **Debian/Ubuntu packaging** cleanup now that PyPI / vendored `i2plib` is not a runtime requirement.

The sections below remain as the **design record** and checklist; treat phased migration
steps as **historical** where they already landed in tree.

## Goal

Replace the runtime dependency on vendored `i2plib` with a small internal SAM
transport layer implemented inside `I2PChat`.

This plan is intentionally Debian/Ubuntu-friendly:

- no vendored `i2plib` required at runtime;
- no `sys.path` injection for `vendor/i2plib`;
- no separate packaging blocker around an outdated external `i2plib`;
- all SAM behavior lives in the application source tree and is built from source.

The design below is intentionally stronger than a minimal one-file SAM helper:

- protocol parsing is separated from socket lifecycle;
- typed exceptions replace generic runtime errors;
- Destination handling is explicit and testable;
- BlindBox and main chat share one transport boundary instead of duplicating SAM logic.

## Cleanup Plan

Before changing runtime behavior:

1. Add a new internal `i2pchat.sam` package with a compatibility-shaped API.
2. Route core and BlindBox imports through `i2pchat.sam` without changing behavior yet.
3. Add parser and destination tests first.
4. Implement the internal backend behind the compatibility API.
5. Switch runtime to the internal backend.
6. Remove `vendor/i2plib` path injection and direct imports.
7. Run tests against real `i2pd >= 2.59.0`.

## Target Module Layout

Add these files:

- `i2pchat/sam/__init__.py`
- `i2pchat/sam/errors.py`
- `i2pchat/sam/destination.py`
- `i2pchat/sam/protocol.py`
- `i2pchat/sam/client.py`
- `i2pchat/sam/backend.py`

## Public API Shape

Expose a compatibility-shaped surface from `i2pchat.sam`:

- `Destination`
- `SAMError`
- `ProtocolError`
- `InvalidId`
- `CantReachPeer`
- `KeyNotFound`
- `DuplicatedId`
- `SessionClosed`
- `new_destination(...)`
- `create_session(...)`
- `dest_lookup(...)`
- `naming_lookup(...)`
- `stream_connect(...)`
- `stream_accept(...)`

This keeps migration cost low because current call sites already use nearly this
exact shape.

## Responsibilities By File

### `errors.py`

Owns typed exceptions and `RESULT=...` mapping.

Minimum classes:

- `SAMError`
- `ProtocolError`
- `InvalidId`
- `CantReachPeer`
- `KeyNotFound`
- `DuplicatedId`
- `SessionClosed`

Required helper:

- `map_result_to_error(result: str, message: str, raw_line: str | None = None)`

## `destination.py`

Owns I2P destination normalization and derived properties.

Required behavior:

- normalize I2P base64 alphabet (`-`/`~`) vs standard base64 (`+`/`/`);
- expose canonical `.base64`;
- derive `.base32` from decoded bytes using `sha256`;
- support private-key blobs with `has_private_key=True`;
- expose `.private_key.base64` for current core usage.

This file must stay small and pure. No sockets, no logging, no business logic.

## `protocol.py`

Owns SAM line construction and reply parsing.

Required builders:

- `build_hello(...)`
- `build_dest_generate(...)`
- `build_naming_lookup(...)`
- `build_session_create(...)`
- `build_stream_connect(...)`
- `build_stream_accept(...)`

Required parsing helpers:

- `parse_reply_line(...)`
- `expect_ok(...)`

Rules:

- reject empty or multiline tokens;
- do not build SAM commands by ad hoc string interpolation at call sites;
- centralize token validation and escaping;
- keep reply parsing deterministic and strict.

## `client.py`

Owns async SAM control connection lifecycle and the session socket.

Required pieces:

- `SAMClient`
- `SessionHandle`
- `open_stream_connect(...)`
- `open_stream_accept(...)`

`SAMClient` should own:

- opening TCP connection to SAM;
- HELLO handshake;
- command write + reply read;
- `DEST GENERATE`;
- `NAMING LOOKUP`;
- `DEST LOOKUP`;
- `SESSION CREATE`.

`SessionHandle` must preserve the long-lived socket required by the SAM session.

## `backend.py`

Owns the public convenience API used by core and BlindBox.

This file should look close to the old `i2plib` shape but be implemented using
the internal client.

`create_session(...)` must return a live session handle and not auto-close the
socket, because main chat depends on that lifecycle behavior.

## Migration Plan

### Phase 1: Introduce API Boundary

Do not remove `i2plib` yet.

First change imports in:

- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/blindbox/blindbox_client.py`

from direct `i2plib` imports to:

```python
from i2pchat import sam as i2plib
```

This gives a low-risk migration seam.

### Phase 2: Reuse Good Existing BlindBox SAM Logic

Existing BlindBox code already has solid operational pieces:

- HELLO timeout handling;
- SESSION CREATE timeout handling;
- cleanup on failure;
- session-id isolation;
- cautious retry behavior.

These parts should be extracted and shared instead of being rewritten from
scratch.

Primary source file:

- `i2pchat/blindbox/blindbox_client.py`

## Phase 3: Implement Internal Backend

Implement internal `i2pchat.sam` behavior behind the compatibility API.

At this point direct `i2plib` usage should already be removed from runtime
transport paths.

## Phase 4: Remove Direct `i2plib` Runtime Dependency

After core and BlindBox work through `i2pchat.sam`:

- remove direct `i2plib` imports from runtime code;
- remove `vendor/i2plib` path injection in `i2pchat/__init__.py`;
- stop relying on `vendor/i2plib` for normal app startup.

## Current `i2plib` Usage Map

These are the main runtime operations currently used:

- `Destination`
- `dest_lookup`
- `new_destination`
- `create_session`
- `stream_connect`
- `stream_accept`
- `naming_lookup`
- `InvalidId`
- `CantReachPeer`

This is a small enough footprint that replacement is realistic.

## Files That Will Need Direct Editing

### `i2pchat/__init__.py`

Current role:

- injects `vendor/i2plib` into `sys.path`
- applies a macOS asyncio `TCP_NODELAY` patch

Target state:

- keep the macOS asyncio patch if still needed;
- remove all `vendor/i2plib` path logic.

### `i2pchat/core/i2p_chat_core.py`

Main touchpoints:

- identity loading from stored private destination;
- generating new destination;
- creating long-lived SAM stream session;
- waiting for self-lookup while tunnels build;
- outgoing stream connect;
- incoming stream accept;
- peer destination normalization;
- explicit naming lookups.

### `i2pchat/blindbox/blindbox_client.py`

Main touchpoints:

- BlindBox session startup;
- HELLO and SESSION CREATE handling;
- destination lookup and connect order;
- error normalization;
- stream opening.

## Runtime Requirements

Target runtime requirement:

- `i2pd >= 2.59.0`

The internal backend should be tested against that floor and should not assume
bundled router binaries exist.

## Tests To Add First

Add these tests before fully switching runtime:

- `tests/test_sam_protocol.py`
- `tests/test_sam_destination.py`

Suggested coverage:

- parse `HELLO REPLY RESULT=OK VERSION=...`
- parse `NAMING REPLY RESULT=OK VALUE=...`
- parse `STREAM STATUS RESULT=CANT_REACH_PEER`
- parse `SESSION STATUS RESULT=DUPLICATED_ID`
- reject malformed or incomplete replies
- derive `.base32` from canonical base64 destination
- preserve private destination blob access

## Tests To Add Next

After parser and destination tests:

- `tests/test_sam_backend.py`
- `tests/test_sam_i2pd_integration.py`

Suggested coverage:

- mocked HELLO/session/connect flows;
- session handle stays open;
- timeout handling closes failed sockets;
- `dest_lookup(.b32.i2p)` behavior;
- connect retry semantics for peer reachability;
- optional real integration with local `i2pd >= 2.59.0`.

## Non-Goals

Do not do these in the first migration PR:

- rewrite chat protocol;
- redesign BlindBox itself;
- change identity file format;
- mix Debian packaging edits into the same runtime refactor;
- collapse protocol parsing and app business logic into one file.

## Completion Criteria

The migration is complete when all of these are true:

1. Runtime code no longer imports `i2plib` directly.
2. `i2pchat/__init__.py` no longer alters `sys.path` for vendored `i2plib`.
3. Core chat and BlindBox both use `i2pchat.sam`.
4. Tests pass without runtime dependence on vendored `i2plib`.
5. Real runtime works against `i2pd >= 2.59.0`.
6. Debian/Ubuntu packaging no longer needs a separate `i2plib` strategy.

## Suggested Commit Sequence

1. Add `i2pchat.sam` skeleton and tests for parser/destination.
2. Route core and BlindBox imports through `i2pchat.sam`.
3. Implement internal backend.
4. Add mocked backend tests.
5. Add real `i2pd` integration tests.
6. Remove vendored import path logic.
7. Clean up docs and packaging assumptions.

## Cursor Handoff Prompt

Use this prompt in Cursor when continuing hardening and packaging prep:

```text
Continue from docs/SAM_INTERNAL_BACKEND_PLAN.md (Status section) and docs/CURSOR_SAM_MILESTONE_REPORT.md.

Constraints:
- Do not reintroduce vendored i2plib as a runtime dependency.
- Keep SAM logic split across errors.py, destination.py, protocol.py, client.py, and backend.py.
- Target i2pd >= 2.59.0.

Next tasks:
1. remove stale i2plib wording from comments and user-facing docs where it implies runtime use;
2. add tests for malformed SAM replies and timeout/socket cleanup paths;
3. then packaging-side cleanup for Debian/Ubuntu (no separate i2plib package strategy).
```
