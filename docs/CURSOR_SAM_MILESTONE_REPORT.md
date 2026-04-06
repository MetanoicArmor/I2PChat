# Cursor SAM Milestone Report

## What Was Done

First migration milestone is now on disk.

Added a new internal boundary package:

- `i2pchat/sam/__init__.py`
- `i2pchat/sam/errors.py`
- `i2pchat/sam/destination.py`
- `i2pchat/sam/protocol.py`
- `i2pchat/sam/client.py`
- `i2pchat/sam/backend.py`

Current behavior is intentionally conservative:

- the public SAM boundary is now fully internal for chat runtime operations;
- `core` and `BlindBox` now import through `i2pchat.sam`;
- the new package creates the seam needed for the future internal transport swap.
- `Destination` is now a real internal implementation instead of a direct alias.
- `naming_lookup`, `dest_lookup`, and `new_destination` now run through the
  internal `SAMClient`.
- `stream_connect` and `stream_accept` now also run through the internal client.
- `create_session()` now also runs through the internal client while preserving
  the long-lived `(reader, writer)` session socket contract used by core.
- `i2pchat/__init__.py` no longer injects vendored `i2plib` into `sys.path`.
- protocol builders and SAM errors no longer depend on `i2plib` imports.

## Runtime Files Updated

- `i2pchat/core/i2p_chat_core.py`
- `i2pchat/blindbox/blindbox_client.py`

BlindBox now uses:

- `from i2pchat import sam as i2plib`
- `from i2pchat.sam import protocol as sam_protocol`

instead of importing `i2plib` and `i2plib.sam.session_create` directly.

## Tests Updated

- `tests/test_sam_input_validation.py`
- `tests/test_blindbox_client.py`
- `tests/test_sam_protocol.py`
- `tests/test_sam_destination.py`
- `tests/test_sam_backend.py`

These now target the new boundary layer instead of importing SAM builders from
`i2plib` directly.

## Important Constraints

- Do not reintroduce **`i2plib`** as a **runtime** dependency (PyPI or `sys.path` shim).
- Prefer **`i2pchat.sam`** for any new SAM-facing code; keep core and BlindBox behavior stable.

## Best Next Step

1. scrub remaining **non-runtime** `i2plib` references in comments and docs (wording only);
2. add more **malformed-reply** and **timeout cleanup** tests around `i2pchat.sam`;
3. then **Debian/Ubuntu packaging** cleanup (no separate `i2plib` strategy).

## Good Prompt For Cursor

```text
Continue the internal SAM migration from docs/SAM_INTERNAL_BACKEND_PLAN.md and docs/CURSOR_SAM_MILESTONE_REPORT.md.

Current state:
- i2pchat.sam exists as a boundary layer.
- core and BlindBox already import through i2pchat.sam.
- public SAM runtime operations are now internal.
- Destination and parser tests now exist and pass.
- naming_lookup, dest_lookup, and new_destination are already internal.
- stream_connect and stream_accept are already internal.
- create_session is now internal and keeps the long-lived session socket contract.
- vendored import-path injection is removed from runtime package bootstrap.
- the temporary legacy backend file is removed.

Next task:
1. finish doc/comment wording cleanup that still implies vendored `i2plib` is the live SAM stack,
2. extend malformed-reply and timeout cleanup test coverage,
3. proceed to packaging-side cleanup for Debian/Ubuntu,
4. keep tests green after each change.
```
