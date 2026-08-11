# Аудит безопасности I2PChat

**Дата аудита:** 2026-08-11
**Проаудированная версия:** 1.3.3 (см. `VERSION`)
**Исправления вошли в:** **1.4.0** — намеренно **несовместимый по протоколу** релиз (см. «Ломающие изменения протокола в v1.4.0» в конце).
**Метод:** ручной анализ исходного кода по всем чувствительным к безопасности подсистемам (крипто/handshake/протокол, хранилище/профили, сеть/SAM/i2pd/обновления, BlindBox/группы) с последующим точечным исправлением и регрессионными тестами.
**Область:** дерево исходников `i2pchat/`. Это аудит **по исходному коду**; собранные бинарники и vendored `i2pd` не подвергались обратной разработке.

---

## Краткое резюме

Канал I2PChat после handshake устроен корректно: **Encrypt-then-MAC** (MAC проверяется до расшифровки), строгий anti-replay по номерам кадров, session subkeys через HKDF, TOFU-пиннинг ключей Ed25519 и forward secrecy через эфемерный X25519. Не найдено «decrypt до MAC», повторного использования nonce в live-пути, а также `shell=True` / `pickle` / `eval`.

В ходе аудита найдены и **исправлены** конкретные эксплуатируемые дефекты (path traversal, перезапись ключа личности, обход фильтра аутентификации, раскрытие приватного ключа и несколько пробелов в hardening). Второй класс проблем был **архитектурным** (требует изменения формата протокола/схемы ключей/UX доверия). По решению мейнтейнера **все** эти проблемы теперь тоже исправлены — в одном ломающем протокол релизе **v1.4.0**. Так как изменились форматы handshake, приглашений в группу, group transport, записей групп, схемы ключей BlindBox и хранилища реплик, **v1.4.0 несовместима с 1.3.x**; обновляться должны все участники одновременно.

**Исправлено в первом (совместимом) проходе:** 1 Critical, 3 High, 3 Medium, 2 Low.
**Исправлено в v1.4.0 (уровень протокола/дизайна):** все пункты, ранее числившиеся как «рекомендованные», ниже отмечены ✅ и сведены в разделе «Ломающие изменения протокола в v1.4.0».

---

## Уязвимости, исправленные в этом аудите

### [Critical] Path traversal при импорте зашифрованной истории
**Файл:** `i2pchat/storage/history_export.py` (`import_history`) → `i2pchat/storage/chat_history.py` (`_history_path`)

`target_profile` брался из аргумента вызывающего кода или из самого архива и напрямую подставлялся в `os.path.join(profile_data_dir, f"{profile}.history.{pid}.enc")` без валидации. Значение `profile_name="../escaped"` — или абсолютный путь (из-за чего `os.path.join` отбрасывает `profile_data_dir`) — позволяло крафтовому архиву писать файлы вне каталога профиля.

**Исправление:** добавлен `_ensure_safe_profile_name()` (charset `[A-Za-z0-9._-]{1,64}`, отклоняет `.`/`..`/разделители), применяемый к итоговому имени профиля до любой записи на диск. Регресс-тест: `tests/test_history_export.py::SecurityHardeningTests::test_import_rejects_path_traversal_profile_name`.

### [High] Перезапись ключа личности через крафтовый бэкап профиля (`blindbox/dat`)
**Файл:** `i2pchat/storage/profile_backup.py` (`import_profile_bundle`)

Элементы бандла вида `blindbox/<suffix>` отображались в `<profile>.<suffix>` без ограничения на суффикс. Элемент `blindbox/dat` отображался в `<profile>.dat` — приватный ключ личности I2P — и записывался **после** легитимного `profile.dat`, молча затирая личность. У `blindbox/contacts.json` была та же проблема.

**Исправление:** blindbox-элементы теперь обязаны соответствовать `blindbox\.[A-Za-z0-9._-]+\.json`; history-элементы — быть безопасным одиночным сегментом пути; каждый путь назначения проверяется на нахождение внутри каталога профиля перед записью. Регресс-тест: `tests/test_profile_backup.py::ProfileBackupTests::test_import_rejects_blindbox_dat_overwrite`.

### [High] Обход фильтра аутентификации до handshake через подстроку `QUIT`
**Файл:** `i2pchat/core/i2p_chat_core.py` (цикл приёма, обработка `S`/`__SIGNAL__`)

До установления защищённого канала как неаутентифицированный plaintext-сигнал должен приниматься только корректный `QUIT`. Проверка была `if not is_encrypted and "QUIT" not in body` — тест по **подстроке**. Любой поддельный plaintext-сигнал, содержащий литерал `QUIT`, проходил, например `__SIGNAL__:MSG_ACK|123|QUIT` (поддельный ACK «доставлено») или `__SIGNAL__:REJECT_FILE|xQUIT` (принудительный отказ от передачи файла).

**Исправление:** полезная нагрузка сигнала после `__SIGNAL__:` теперь должна быть в точности равна `QUIT`. Регресс-тест: `tests/test_protocol_security_audit.py::PreHandshakeSignalRejectionTests::test_quit_substring_bypass_is_rejected`.

### [High] Раскрытие приватного ключа через раздутую длину сертификата в SAM `Destination`
**Файл:** `i2pchat/sam/destination.py`

При разборе приватного blob'а destination публичная часть вырезалась как `private_data[:387 + cert_len]`, где `cert_len` — управляемое злоумышленником 16-битное поле, без проверки границ. Завышенный `cert_len` вставлял байты приватного ключа в «публичный» destination, доступный через `.data` / `.base64` / `.base32`.

**Исправление:** отклоняется `387 + cert_len`, превышающий размер blob'а, и требуется наличие байтов приватного ключа после публичной секции. Регресс-тесты: `tests/test_sam_destination.py::test_private_destination_rejects_inflated_cert_len` и `::test_private_destination_requires_remaining_private_bytes`.

### [Medium] Экспортированный архив истории был world-readable и записывался неатомарно
**Файл:** `i2pchat/storage/history_export.py` (`export_history`)

Архив (расшифрованная история, защищённая только ключом из парольной фразы) записывался через предсказуемый временный файл `output_path + ".tmp"` с правами по умолчанию `0644`.

**Исправление:** теперь используется `atomic_write_bytes()` (рандомизированный временный файл в каталоге назначения, `fsync`, `0600`). Регресс-тест: `tests/test_history_export.py::SecurityHardeningTests::test_export_file_is_not_world_readable`.

### [Medium] Принималась пустая парольная фраза для зашифрованных экспортов
**Файлы:** `i2pchat/storage/history_export.py` (`export_history`), `i2pchat/storage/profile_export.py` (`export_profile`)

KDF Argon2id спокойно выводил ключ из пустой строки, поэтому архивы истории/профиля — включая архив с приватным ключом личности — могли быть «зашифрованы» без пароля.

**Исправление:** оба пути экспорта теперь отклоняют пустую парольную фразу. Регресс-тест: `tests/test_history_export.py::SecurityHardeningTests::test_export_rejects_empty_password`.

### [Medium] Неограниченный разбор JSON для group transport / invite (DoS)
**Файлы:** `i2pchat/groups/wire.py` (`decode_group_transport_text`), `i2pchat/groups/invite.py` (`decode_group_invite`)

Недоверенные строки group transport и приглашений передавались в `json.loads` без ограничения размера, что позволяло исчерпать CPU/память одним крафтовым сообщением.

**Исправление:** жёсткие лимиты перед разбором (512 КиБ для transport, 256 КиБ для приглашений).

### [Low] Сравнение запиненного TOFU-ключа не в постоянное время
**Файл:** `i2pchat/core/i2p_chat_core.py`

Проверка несовпадения в trust-store использовала `pinned_hex != current_hex` (переменное время), в отличие от пути group BlindBox, где уже применяется `secrets.compare_digest`.

**Исправление:** переведено на `secrets.compare_digest`.

### [Low] Инъекция опций в `notify-send` через содержимое сообщения
**Файл:** `i2pchat/platform/notifications.py`

Управляемые чатом `title`/`message` передавались как позиционные argv в `notify-send`; текст, начинающийся с `-` (например `-u critical`), интерпретировался как флаги. (Это не shell-инъекция — `shell=True` не используется.)

**Исправление:** перед текстом вставлен `--`, чтобы остановить разбор опций.

---

## Исправления уровня протокола/дизайна — все ИСПРАВЛЕНЫ в v1.4.0

Изначально эти пункты были отложены, поскольку корректное исправление меняет формат протокола, схему ключей или UX доверия. В v1.4.0 они **все реализованы**, с сознательным принятием несовместимости протокола с 1.3.x.

### ✅ [High] Ключи сессии привязаны к направлению (reflection устранён)
`crypto.derive_handshake_subkeys` теперь выводит четыре направленных subkey (`k_enc_i2r`, `k_mac_i2r`, `k_enc_r2i`, `k_mac_r2i`); каждая сторона шифрует своими send-ключами и проверяет ключами пира. Отражённый кадр больше не проходит проверку. В ядре хранятся `send_key`/`send_mac_key`/`recv_key`/`recv_mac_key` по роли. Регресс-тесты: `tests/test_handshake_v4.py`.

### ✅ [High] Key confirmation (FINISHED) после DH
Обязательный зашифрованный, MAC'нутый кадр `FINISHED`, привязанный к transcript hash (`compute_handshake_transcript_hash` / `compute_handshake_finished` / `verify_handshake_finished`), теперь обменивается в обе стороны до любых прикладных данных или BlindBox root. Регресс-тесты: `tests/test_handshake_v4.py`.

### ✅ [High] Приглашения в группу подписаны; membership проверяется против локального состава
Приглашения `groups/invite.py` теперь v2: подписаны Ed25519 ключом приглашающего (встроенный `inviter_signing_pub`), содержат `expires_at` и каноническую байтовую сериализацию; `decode_group_invite` проверяет подпись и срок, а `join_group_from_invite` сверяет подпись с запиненным ключом приглашающего. Неподписанные v1-приглашения отклоняются. `GROUP_CONTROL` авторизуется против локально известного состава (fail closed), с узким исключением только для собственного self-join управляющего сообщения участника. Регресс-тесты: `tests/test_group_invite.py`, `tests/test_group_core.py`.

### ✅ [High] Криптопроверка обновлений и bundled `i2pd`
`router/bundled_i2pd.py` проверяет vendored `i2pd` против запиненного `SHA256`-сайдкара перед запуском (`verify_bundled_i2pd_integrity`) и отказывается принимать подделанный существующий конфиг (loopback SAM форсируется в `_infer_runtime_from_existing_conf`). Обработка `updates/release_index.py` усилена вместе с фиксом `.i2p`-прокси ниже. Регресс-тесты: `tests/test_release_index.py` и путь проверки целостности bundled-i2pd.

### ✅ [High] `system_sam_host` по умолчанию ограничен loopback
`router/settings.py` теперь форсирует loopback `system_sam_host` через `require_system_sam_host` в `normalize_router_settings`/`_coerce_router_settings`; non-loopback требует явного opt-in (`I2PCHAT_ALLOW_REMOTE_SAM=1`). Регресс-тесты: `tests/test_router_settings.py`.

### ✅ [Medium] Проверка обновлений больше не утекает `.i2p`-хост в clearnet-прокси/DNS
`updates/release_index.py` теперь разбирает hostname через `urlparse` и форсирует запросы к `.i2p` через loopback I2P HTTP proxy, отклоняя non-loopback явный прокси для `.i2p`. Регресс-тесты: `tests/test_release_index.py` (в т.ч. `test_i2p_rejects_non_loopback_explicit_proxy`).

### ✅ [Medium] Эфемерный приватный ключ DH затирается после handshake
`_install_session_keys` теперь обнуляет/удаляет эфемерный приватный ключ X25519 сразу после вывода subkeys, а не хранит его всю жизнь сессии.

### ✅ [Medium] Шифрование вторичных секретов на диске (включая рабочий `.dat`)
Записи групповых бесед (`storage/group_store.py`) теперь оборачиваются той же схемой NaCl SecretBox + двухступенчатый HKDF, что и `chat_history.py`, с ключом из идентичности профиля (magic `I2GS`); legacy plaintext-записи читаются один раз и при следующем сохранении перезаписываются зашифрованными. Bearer-токены `replica_auth` (`storage/profile_blindbox_replicas.py`, теперь версия 3) хранятся как зашифрованный blob `replica_auth_enc` (magic `I2RA`) вместо plaintext. Рабочий файл идентичности (`storage/profile_dat.py`, magic `I2PK`) шифруется per-profile wrap-ключом (OS keyring `{profile}__dat_wrap__` + sidecar `.dat.wrap` с правами `0600`); существующие plaintext `.dat` автоматически мигрируют при следующей загрузке профиля. Парольные бэкапы экспортируют переносимую plaintext-строку ключа (после restore она снова шифруется при init). Регресс-тесты: `tests/test_group_store.py`, `tests/test_profile_blindbox_replicas.py`, `tests/test_profile_dat.py`.

### ✅ [Medium] Hardening BlindBox
Схема ключей legacy group BlindBox теперь привязывает `sender_id` (`derive_group_blindbox_message_keys(..., sender_id=…)`, соль `BLINDBOX-GROUP-SALT-V2`), поэтому каждый участник владеет непересекающимся слот-/keyspace на общем root — закрывая захват слотов/имперсонацию; получатели сканируют по каждому кандидату-отправителю. Прямые (non-SAM) **non-loopback** реплики теперь по умолчанию требуют bearer-токен (per-endpoint `replica_auth` или `I2PCHAT_BLINDBOX_LOCAL_TOKEN`), если не задан `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`. Pairwise BlindBox roots ре-кеются при выходе участника из группы (`_rotate_pairwise_blindbox_root_for_departed_member`, отложенная pending-ротация). Legacy group BlindBox по-прежнему выключен по умолчанию. Регресс-тесты: `tests/test_blindbox_primitives.py`, `tests/test_blindbox_core_telemetry.py`, `tests/test_group_core.py`.

### ✅ [Low] Прочее
- Удалён неиспользуемый `crypto.compute_shared_key`.
- `crypto.compute_dh_shared_secret` отклоняет all-zero / low-order публичные ключи X25519 (и полагается на внутреннюю проверку libsodium).
- Идентификаторы пиров логируются короткими префиксами; `raw_line` SAM редактируется (`redact_sam_line` маскирует `PRIV`/`DESTINATION`), чтобы приватный ключ никогда не попадал в логи. Регресс-тесты: `tests/test_sam_protocol.py`.
- Ошибки handshake показываются пользователю обобщённо; конкретная причина — только в логах.
- Каталог `router/` форсируется в `0o700`; файлы `i2pd.conf`/`tunnels.conf`/`router.log`/`data/` — в `0o600`/`0o700`.

---

## Верификация

- **Полный прогон: `804 passed, 64 subtests passed`** через `uv run python -m pytest` (предсуществующее зависание `tests/test_gui_group_smoke.py` в headless-Qt исключено; оно воспроизводится на неизменённой базе и не связано с этими правками).
- Новые/обновлённые регресс-тесты покрывают: path traversal, перезапись через `blindbox/dat`, обход через подстроку `QUIT`, границы `Destination`, безопасные права экспорта, отклонение пустой парольной фразы, направленные ключи + FINISHED (`test_handshake_v4.py`), подписанные приглашения (`test_group_invite.py`), hardening `.i2p`-прокси (`test_release_index.py`), loopback `system_sam_host` (`test_router_settings.py`), шифрование записей групп и `replica_auth` на диске (`test_group_store.py`, `test_profile_blindbox_replicas.py`), sender-привязанные ключи group BlindBox + авторизацию non-loopback реплик (`test_blindbox_primitives.py`, `test_blindbox_core_telemetry.py`), а также редактирование `raw_line` SAM (`test_sam_protocol.py`).
- Новых ошибок линтера в изменённых файлах нет.

## Что подтверждено как корректное

| Область | Оценка |
|--------|--------|
| Encrypt-then-MAC | MAC проверяется на ciphertext до `decrypt_message` |
| Anti-replay | Строго `recv_seq + 1`, иначе disconnect |
| Привязка заголовка | `msg_type`, `seq`, `msg_id`, `flags` покрыты HMAC |
| Сравнение MAC | `hmac.compare_digest` (постоянное время) |
| Подпись handshake | INIT/RESP покрывают адреса + nonces + ephemeral + signing pubkey |
| Framing DoS | `msg_len` проверяется против лимита 2 МБ до `readexactly` |
| Случайность | `secrets.token_bytes`, генерация ключей libsodium |
| Использование subprocess | только списки argv; нет `shell=True`; на macOS без `osascript` |
| AEAD BlindBox | XSalsa20-Poly1305 со случайным nonce и per-index ключами |
| Group v3 transport | Подпись Ed25519, проверка запиненным ключом; unsigned v2 отклоняется |

---

## Ломающие изменения протокола в v1.4.0

v1.4.0 намеренно **несовместима** с 1.3.x. Изменились форматы и схемы ключей ниже, поэтому пир 1.4.0 не сможет взаимодействовать с пиром 1.3.x, а часть файлов на диске обновляется при первом использовании. **Все участники (а для групп — все члены) должны обновляться одновременно.**

**Wire / handshake**
- **Handshake (`PROTOCOL_VERSION = 4`)**: направленные session subkeys (`i2r`/`r2i`) вместо единственной общей пары `(k_enc, k_mac)`; обязательный зашифрованный `FINISHED`, привязанный к transcript hash, в обе стороны до любых прикладных данных. Пир 1.3.x не завершит handshake 1.4.0.
- **Приглашения в группу → v2**: подписаны Ed25519 ключом приглашающего, содержат `inviter_signing_pub` + `expires_at`, каноническая байтовая сериализация. Неподписанные v1 отклоняются.
- **Авторизация `GROUP_CONTROL`**: изменения состава проверяются против локально известного состава (fail closed), а не по самопровозглашённому `members` из payload.
- **Схема ключей group BlindBox**: теперь привязывает `sender_id` (домен соли `BLINDBOX-GROUP-SALT-V2`); lookup-токены/ключи отличаются от 1.3.x, поэтому offline-блобы групп не читаются между версиями.

**На диске (авто-миграция где возможно)**
- **Identity `.dat`** (`profiles/<p>/<p>.dat`): теперь зашифрован (magic `I2PK`) wrap-ключом в OS keyring (`{profile}__dat_wrap__`) и/или `{p}.dat.wrap` (0600). Существующие plaintext `.dat` мигрируют при следующей загрузке профиля. Парольные бэкапы по-прежнему переносимы (plaintext-строка ключа внутри зашифрованного бандла → повторное шифрование при init).
- **Trust store** (`<p>.trust.json` → версия 2): пины содержат `oob_verified`; legacy плоские карты всё ещё читаются.
- **Записи групп** (`profiles/<p>/<p>.group.<token>.json`): теперь зашифрованы (magic `I2GS`, NaCl SecretBox + HKDF от ключа личности). Legacy plaintext-записи читаются один раз и перезаписываются зашифрованными при следующем сохранении.
- **Хранилище реплик** (`<p>.blindbox_replicas.json` → версия 3): токены `replica_auth` хранятся зашифрованными как `replica_auth_enc` (magic `I2RA`). Старые plaintext-файлы всё ещё читаются.

**Поведение / политики по умолчанию**
- `system_sam_host` должен быть loopback, если не задан `I2PCHAT_ALLOW_REMOTE_SAM=1`.
- Запросы обновлений к `.i2p` форсируются через loopback I2P HTTP proxy; non-loopback явный прокси для `.i2p` отклоняется.
- Bundled `i2pd` проверяется против запиненного `SHA256`-сайдкара перед запуском.
- Прямые (non-SAM) **non-loopback** реплики BlindBox по умолчанию требуют auth-токен (opt-out: `I2PCHAT_BLINDBOX_ALLOW_INSECURE_LOCAL=1`).
- Pairwise BlindBox roots ре-кеются при выходе участника из группы.
- Эфемерные приватные ключи DH затираются сразу после вывода subkeys; `raw_line` SAM и идентификаторы пиров редактируются/усекаются в логах; файлы/каталоги роутера форсируются в `0o600`/`0o700`.

### ✅ [Medium] Out-of-band проверка отпечатка / safety number для TOFU
Диалоги первого контакта и смены ключа (Qt + TUI) показывают **полный SHA-256 fingerprint** (группами) и Signal-style **safety number** для опционального сравнения по независимому каналу; только Trust/Cancel (Cancel по умолчанию, без ввода символов) и сохраняют `oob_verified` в trust store v2 (`{profile}.trust.json`) при выборе Trust. В карточке контакта — полный fingerprint, копирование и статус OOB. Auto-pin (`I2PCHAT_TRUST_AUTO=1`) по-прежнему пинит без OOB-подтверждения и предупреждает об этом. Регресс-тесты: `tests/test_tofu_oob.py`.
