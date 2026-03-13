# I2PChat v2.0 Security Release

## Обзор

Данный релиз полностью переработан с точки зрения безопасности. Реализованы защитные меры против всех выявленных уязвимостей протокола, добавлено end-to-end шифрование с Perfect Forward Secrecy.

**Протокол**: v2 (несовместим с v1)

---

## Сравнение: до и после

### Протокол сообщений

| Аспект | v1 (было) | v2 (стало) |
|--------|-----------|------------|
| Формат фрейма | `TYPE(1) + LEN(4) + BODY + \n` | `TYPE(1) + [E] + LEN(4-6) + BODY + [HMAC(32)] + \n` |
| Шифрование | Нет (только I2P транспорт) | XSalsa20-Poly1305 (NaCl SecretBox) |
| Проверка целостности | Нет | HMAC-SHA256 |
| Аутентификация | Только I2P destination | Challenge-response + nonce exchange |
| Perfect Forward Secrecy | Нет | Эфемерные X25519 ключи |

### Защитные меры

| Уязвимость | v1 (было) | v2 (стало) |
|------------|-----------|------------|
| Зависание на чтении | Нет защиты | Таймаут 30 сек на все операции |
| OOM через image_buffer | Неограничен | Лимит 500 строк |
| Заполнение диска файлами | Неограничен | Лимит 50 MB |
| Path traversal | Частичная защита | Sandbox директория + sanitize |
| Plaintext ключи | `chmod` директории | `chmod 600` файлов + keyring |
| Логирование | Тихий break | logging с деталями |

---

## Детали изменений

### 1. Защита от DoS

#### 1.1 Таймауты на сетевые операции
```python
# БЫЛО:
len_data = await reader.readexactly(4)

# СТАЛО:
len_data = await asyncio.wait_for(
    reader.readexactly(4), timeout=self.READ_TIMEOUT  # 30 сек
)
```

#### 1.2 Ограничение буфера изображений
```python
# БЫЛО:
self.image_buffer.append(body)  # без ограничений

# СТАЛО:
MAX_IMAGE_LINES = 500
if len(self.image_buffer) < self.MAX_IMAGE_LINES:
    self.image_buffer.append(body)
else:
    self._emit_error("Image too large, truncating")
```

#### 1.3 Ограничение размера файлов
```python
# БЫЛО:
self.incoming_file = open(safe_name, "wb")  # любой размер

# СТАЛО:
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
if size > self.MAX_FILE_SIZE:
    self._emit_error(f"File too large: {size} bytes")
    continue
```

### 2. Безопасность файловой системы

#### 2.1 Sandbox для загрузок
```python
# БЫЛО:
safe_name = f"recv_{filename}"
self.incoming_file = open(safe_name, "wb")  # в текущей директории

# СТАЛО:
def get_downloads_dir() -> str:
    base = os.path.join(get_profiles_dir(), "downloads")
    os.makedirs(base, exist_ok=True)
    os.chmod(base, 0o700)
    return base

safe_path = os.path.join(get_downloads_dir(), safe_name)
```

#### 2.2 Валидация имён файлов
```python
# БЫЛО:
filename = os.path.basename(filename)

# СТАЛО:
SAFE_FILENAME_RE = re.compile(r'^[\w\-. ]+$')

def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    if not name or not SAFE_FILENAME_RE.match(name) or name.startswith('.'):
        return f"file_{int(time.time())}"
    if len(name) > 200:
        return f"file_{int(time.time())}{ext}"
    return name
```

### 3. Защита ключей

#### 3.1 Права доступа
```python
# БЫЛО:
with open(key_file, "w") as f:
    f.write(dest.private_key.base64 + "\n")

# СТАЛО:
with open(key_file, "w") as f:
    f.write(dest.private_key.base64 + "\n")
os.chmod(key_file, 0o600)  # только владелец
```

#### 3.2 Интеграция с системным keyring
```python
# БЫЛО: только файловое хранение

# СТАЛО: приоритет keyring, fallback на файл
if _try_keyring_set(self.profile, dest.private_key.base64):
    self._emit_message("success", "Identity saved to secure keyring")
else:
    # fallback to file
```

### 4. Криптография

#### 4.1 Новый модуль `crypto.py`

```python
# HMAC для проверки целостности
def compute_mac(key: bytes, msg_type: str, body: bytes) -> bytes:
    return hmac.new(key, msg_type.encode() + body, hashlib.sha256).digest()

def verify_mac(key: bytes, msg_type: str, body: bytes, mac: bytes) -> bool:
    expected = compute_mac(key, msg_type, body)
    return hmac.compare_digest(expected, mac)  # timing-safe
```

#### 4.2 E2E шифрование (при наличии pynacl)
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

### 5. Secure Handshake (v2)

#### Протокол:
```
1. Initiator -> Responder: H:INIT:<nonce_hex>:<ephemeral_pubkey_hex>
2. Responder -> Initiator: H:RESP:<nonce_hex>:<ephemeral_pubkey_hex>
3. Обе стороны вычисляют:
   - DH shared = X25519(my_ephemeral, peer_ephemeral)
   - shared_key = SHA256(DH_shared || nonce_init || nonce_resp)
4. Включается шифрование всех последующих сообщений
```

#### Диаграмма:
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

### 6. Логирование

```python
# БЫЛО:
if msg_type not in [...]:
    break  # тихий выход

# СТАЛО:
logger = logging.getLogger("i2pchat")

if msg_type not in [...]:
    logger.warning(f"Invalid message type received: {repr(msg_type)}")
    break
```

---

## Новые файлы

| Файл | Описание |
|------|----------|
| `crypto.py` | Криптографический модуль (HMAC, шифрование, DH) |

## Обновлённые зависимости

```
# requirements.txt
+ pynacl  # для E2E шифрования и PFS
```

---

## Режимы работы

### С pynacl (рекомендуется)
- HMAC-SHA256 для всех сообщений
- XSalsa20-Poly1305 шифрование
- X25519 эфемерные ключи (PFS)
- Защита от replay атак

### Без pynacl (fallback)
- HMAC-SHA256 для всех сообщений
- SHA256(nonce_A || nonce_B) как shared_key
- Нет PFS

---

## Миграция

**Важно:** Протокол v2 несовместим с v1. Оба участника чата должны обновиться.

1. Обновить зависимости:
   ```bash
   pip install -r requirements.txt
   ```

2. Существующие профили и ключи совместимы - миграция не требуется.

3. При первом подключении автоматически выполняется secure handshake.

---

## Известные ограничения

1. **Отсутствие подписи эфемерных ключей**: В текущей реализации эфемерные ключи не подписываются долгосрочными Ed25519 ключами I2P. Это планируется в следующем релизе.

2. **Replay атаки**: Защита реализована через nonce, но не через timestamp. При компрометации shared_key старые сообщения могут быть воспроизведены в пределах одной сессии.

3. **Keyring на Windows**: Зависит от наличия Windows Credential Locker.

---

## Рекомендации по безопасности

1. **Всегда используйте pynacl** для полной защиты
2. **Используйте persistent профили** для сохранения ключей
3. **Проверяйте fingerprint пира** при первом соединении
4. **Регулярно обновляйте** приложение
