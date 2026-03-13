# Debug Report: Messages Not Delivered (Protocol v2)

## Problem
Messages are not being delivered between Linux and macOS clients after upgrading to protocol v2 with E2E encryption.

## Evidence from Linux Logs

```
recv_type: "B" (hex 42) — legacy base64 from macOS (first connection)
recv_type: "S" (hex 53) — v2 identity frame (second connection)
hs_init_sent: INIT:4647b5fc... — Linux sent handshake INIT
recv_eof — connection closed by macOS
```

## Root Cause Analysis

1. **Linux sends INIT handshake** (`H` message type) after connection
2. **macOS does NOT respond with RESP** — instead, connection closes (EOF)
3. Without completed handshake, `shared_key` is never set
4. Messages sent without encryption, but receiver may expect encrypted format

## Specific Issues Found

### Issue 1: macOS sends legacy base64 first
- First byte received: `B` (0x42) — this is start of base64 destination
- Protocol v2 on Linux has no fallback for legacy format
- Connection breaks immediately

### Issue 2: Handshake not completing
- Linux sends: `H` frame with `INIT:<nonce>:<ephemeral_pubkey>`
- macOS should respond: `H` frame with `RESP:<nonce>:<ephemeral_pubkey>`
- Instead: EOF (macOS closes connection or doesn't understand `H` type)

## Required Fixes on macOS

### 1. Check `receive_loop` handles type `H`
In `i2p_chat_core.py`, ensure `msg_type == "H"` branch exists and calls `_handle_handshake_message()`:

```python
elif msg_type == "H":
    await self._handle_handshake_message(body, writer)
```

### 2. Check `connect_to_peer` initiates handshake
After sending identity, should call:
```python
loop.create_task(self.initiate_secure_handshake())
```

### 3. Check `_handle_handshake_message` sends RESP
When receiving `INIT:`, should respond with `RESP:`:
```python
if body.startswith("INIT:"):
    # ... parse peer_nonce, peer_ephemeral_public
    response = f"RESP:{self.my_nonce.hex()}:{self.my_ephemeral_public.hex()}"
    writer.write(self.frame_message_plain("H", response))
    await writer.drain()
```

### 4. Remove legacy base64 send in `connect_to_peer`
Old code sends raw base64 before framed message:
```python
# REMOVE THIS LINE:
writer.write(self.my_dest.base64.encode() + b"\n")

# KEEP only framed message:
writer.write(self.frame_message("S", self.my_dest.base64))
```

## Verification Steps

After fixing macOS:
1. Both clients should log `hs_init_sent` or `hs_recv`
2. Both should log `hs_key_set` with matching `key` prefix
3. Messages should log `recv_msg_U` with decrypted content

## Files to Check on macOS
- `i2p_chat_core.py` — main protocol logic
- `crypto.py` — encryption functions (should be identical)
