# I2PChat v2.0 Security Release

## Overview

This release is a full security overhaul. It addresses all identified protocol vulnerabilities and adds end-to-end encryption with Perfect Forward Secrecy.

**Protocol**: v2 (incompatible with v1)

---

## Before and after

### Message protocol

| Aspect | v1 (before) | v2 (after) |
|--------|-------------|------------|
| Frame format | `TYPE(1) + LEN(4) + BODY + \n` | `TYPE(1) + [E] + LEN(4-6) + BODY + [HMAC(32)] + \n` |
| Encryption | None (I2P transport only) | XSalsa20-Poly1305 (NaCl SecretBox) |
| Integrity | None | HMAC-SHA256 |
| Authentication | I2P destination only | Challenge-response + nonce exchange |
| Perfect Forward Secrecy | No | Ephemeral X25519 keys |

### Security measures

| Vulnerability | v1 (before) | v2 (after) |
|---------------|-------------|------------|
| Read hang | No protection | 30s timeout on all operations |
| OOM via image_buffer | Unbounded | 500-line limit |
| Disk fill via files | Unbounded | 50 MB limit |
| Path traversal | Partial | Sandbox directory + sanitize |
| Plaintext keys | Directory chmod | File chmod 600 + keyring |
| Logging | Silent break | Logging with details |

---

## Change details

### 1. DoS protection

#### 1.1 Timeouts on network operations
```python
# BEFORE:
len_data = await reader.readexactly(4)

# AFTER:
len_data = await asyncio.wait_for(
    reader.readexactly(4), timeout=self.READ_TIMEOUT  # 30s
)
```

#### 1.2 Image buffer limit
```python
# BEFORE:
self.image_buffer.append(body)  # no limit

# AFTER:
MAX_IMAGE_LINES = 500
if len(self.image_buffer) < self.MAX_IMAGE_LINES:
    self.image_buffer.append(body)
else:
    self._emit_error("Image too large, truncating")
```

#### 1.3 File size limit
```python
# BEFORE:
self.incoming_file = open(safe_name, "wb")  # any size

# AFTER:
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
if size > self.MAX_FILE_SIZE:
    self._emit_error(f"File too large: {size} bytes")
    continue
```

### 2. Filesystem security

#### 2.1 Sandbox for downloads
```python
# BEFORE:
safe_name = f"recv_{filename}"
self.incoming_file = open(safe_name, "wb")  # current directory

# AFTER:
def get_downloads_dir() -> str:
    base = os.path.join(get_profiles_dir(), "downloads")
    os.makedirs(base, exist_ok=True)
    os.chmod(base, 0o700)
    return base

safe_path = os.path.join(get_downloads_dir(), safe_name)
```

#### 2.2 Filename validation
```python
# BEFORE:
filename = os.path.basename(filename)

# AFTER:
SAFE_FILENAME_RE = re.compile(r'^[\w\-. ]+$')

def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    if not name or not SAFE_FILENAME_RE.match(name) or name.startswith('.'):
        return f"file_{int(time.time())}"
    if len(name) > 200:
        return f"file_{int(time.time())}{ext}"
    return name
```

### 3. Key protection

#### 3.1 File permissions
```python
# BEFORE:
with open(key_file, "w") as f:
    f.write(dest.private_key.base64 + "\n")

# AFTER:
with open(key_file, "w") as f:
    f.write(dest.private_key.base64 + "\n")
os.chmod(key_file, 0o600)  # owner only
```

#### 3.2 System keyring integration
```python
# BEFORE: file storage only

# AFTER: keyring preferred, file fallback
if _try_keyring_set(self.profile, dest.private_key.base64):
    self._emit_message("success", "Identity saved to secure keyring")
else:
    # fallback to file
```

### 4. Cryptography

#### 4.1 New `i2pchat/crypto.py` module

```python
# HMAC for integrity
def compute_mac(key: bytes, msg_type: str, body: bytes) -> bytes:
    return hmac.new(key, msg_type.encode() + body, hashlib.sha256).digest()

def verify_mac(key: bytes, msg_type: str, body: bytes, mac: bytes) -> bool:
    expected = compute_mac(key, msg_type, body)
    return hmac.compare_digest(expected, mac)  # timing-safe
```

#### 4.2 E2E encryption (when pynacl is available)
```python
def encrypt_message(key: bytes, plaintext: bytes) -> bytes:
    box = SecretBox(key)
    return bytes(box.encrypt(plaintext))  # XSalsa20-Poly1305

def decrypt_message(key: bytes, ciphertext: bytes) -> Optional[bytes]:
    box = SecretBox(key)
    return bytes(box.decrypt(ciphertext))
```

#### 4.3 Perfect Forward Secrecy
```python
def generate_ephemeral_keypair() -> Tuple[bytes, bytes]:
    private = PrivateKey.generate()  # X25519
    return bytes(private), bytes(private.public_key)

def compute_dh_shared_secret(my_private: bytes, peer_public: bytes) -> bytes:
    box = Box(PrivateKey(my_private), PublicKey(peer_public))
    return bytes(box.shared_key())
```

### 5. Secure handshake (v2)

#### Protocol:
```
1. Initiator -> Responder: H:INIT:<nonce_hex>:<ephemeral_pubkey_hex>
2. Responder -> Initiator: H:RESP:<nonce_hex>:<ephemeral_pubkey_hex>
3. Both sides compute:
   - DH shared = X25519(my_ephemeral, peer_ephemeral)
   - shared_key = SHA256(DH_shared || nonce_init || nonce_resp)
4. All subsequent messages are encrypted
```

#### Diagram:
```
Initiator                              Responder
    |                                      |
    |-- H:INIT:<nonce_A>:<pubkey_A> ------>|
    |                                      |
    |<----- H:RESP:<nonce_B>:<pubkey_B> ---|
    |                                      |
    |   [shared_key computed on both]      |
    |                                      |
    |====== Encrypted channel open ========|
```

### 6. Logging

```python
# BEFORE:
if msg_type not in [...]:
    break  # silent exit

# AFTER:
logger = logging.getLogger("i2pchat")

if msg_type not in [...]:
    logger.warning(f"Invalid message type received: {repr(msg_type)}")
    break
```

---

## New files

| File | Description |
|------|-------------|
| `i2pchat/crypto.py` | Crypto module (HMAC, encryption, DH) |

## Updated dependencies

```
# requirements.txt
+ pynacl  # for E2E encryption and PFS
```

---

## Operation modes

### With pynacl (recommended)
- HMAC-SHA256 for all messages
- XSalsa20-Poly1305 encryption
- X25519 ephemeral keys (PFS)
- Replay protection

### Without pynacl (fallback)
- HMAC-SHA256 for all messages
- SHA256(nonce_A || nonce_B) as shared_key
- No PFS

---

## Migration

**Important:** Protocol v2 is incompatible with v1. Both peers must upgrade.

1. Update dependencies (today: **uv** + **`uv.lock`**; this release historically used **`pip install -r requirements.txt`**):
   ```bash
   uv sync
   ```

2. Existing profiles and keys are compatible — no migration needed.

3. On first connection, the secure handshake runs automatically.

---

## Known limitations

1. **Ephemeral keys not signed**: In the current implementation, ephemeral keys are not signed by long-term I2P Ed25519 keys. This is planned for a future release.

2. **Replay attacks**: Protection is via nonce, not timestamp. If shared_key is compromised, old messages could be replayed within the same session.

3. **Keyring on Windows**: Depends on Windows Credential Locker availability.

---

## Security recommendations

1. **Always use pynacl** for full protection
2. **Use persistent profiles** to store keys
3. **Verify peer fingerprint** on first connection
4. **Keep the app updated**
