# Security Audit Report: I2PChat

Дата аудита: 2026-03-17  
Режим: полный аудит (код + тесты + CI/build + dependency/supply-chain)  
Область: текущее состояние репозитория `I2PChat` (локальный код и конфигурация; без внешней прод-инфраструктуры).

## Executive Summary

Проведен повторный полный аудит с перепроверкой всех пунктов из предыдущего отчета.

Подтвержденные находки:
- **Critical:** 0
- **High:** 0
- **Medium:** 1
- **Low:** 4

Главный вывод: в протоколе и CI уже закрыта значимая часть ранее найденных рисков (MAC/ACK/downgrade, pinning actions, минимальные permissions), но остаются supply-chain и hardening-вопросы в build-процессе и GUI path-safety.

## Scope и методика

Проверенные компоненты:
- протокол и криптография: `i2p_chat_core.py`, `crypto.py`, `protocol_codec.py`;
- GUI и локальные файловые операции: `main_qt.py`;
- зависимости и сборка: `requirements.in`, `requirements.txt`, `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`;
- CI: `.github/workflows/security-audit.yml`, `.github/workflows/nix-check.yml`;
- security-регрессии: `tests/test_asyncio_regression.py`, `tests/test_protocol_framing_vnext.py`, `tests/test_profile_import_overwrite.py`.

Метод:
- threat-model ревизия (remote peer, локальный process adversary, supply-chain);
- статический анализ trust boundaries и sensitive paths;
- сверка security-claims отчета с фактическим кодом/конфигами;
- запуск security-регрессий.

Фактическая проверка тестов:
- `python3 -m unittest tests/test_asyncio_regression.py tests/test_protocol_framing_vnext.py tests/test_profile_import_overwrite.py` -> **OK (28 tests)**.

---

## Findings

## [MEDIUM] F-01: Build tooling устанавливает `pyinstaller` вне hash-pinned lock-потока

**Затронуто:** `build-linux.sh`, `build-macos.sh`, `build-windows.ps1`  
**Категория:** Supply-chain / Build integrity

### Наблюдение

Во всех build-скриптах выполняется:
- установка runtime-зависимостей через `--require-hashes -r requirements.txt` (корректно),
- затем отдельная установка `pyinstaller` без фиксации версии и без hash verification.

### Влияние

- зависимость, влияющая на финальный release-бинарь, подтягивается по floating latest;
- повышенный риск компрометации сборки при инциденте в цепочке поставки.

### Рекомендации

1. Добавить `pyinstaller` в lock-файл с hashes (например, отдельный `requirements-build.txt`).
2. Ставить build-зависимости только через `--require-hashes`.
3. Для release-сборки использовать отдельный reproducible build profile.

---

## [LOW] F-02: В CI `pip-audit` устанавливается непинованно

**Затронуто:** `.github/workflows/security-audit.yml`  
**Категория:** CI tooling integrity

### Наблюдение

`pip-audit` устанавливается в workflow без version pin/hash pin.

### Влияние

Это не напрямую компрометирует runtime приложения, но снижает надежность security-gate в CI.

### Рекомендации

1. Пиновать версию `pip-audit`.
2. По возможности использовать hash-pinned requirements для CI tooling.

---

## [LOW] F-03: `nix_path` указывает на `nixos-unstable` в workflow

**Затронуто:** `.github/workflows/nix-check.yml`  
**Категория:** Reproducibility hardening

### Наблюдение

В workflow задано `nix_path: nixpkgs=channel:nixos-unstable`.

### Влияние

Потенциальная недетерминированность окружения CI (в зависимости от разрешения каналов), даже при наличии flake-check.

### Рекомендации

1. По возможности полагаться только на `flake.lock`.
2. Убрать/минимизировать channel override, если он не нужен.

---

## [LOW] F-04: Нет явного path confinement перед открытием изображения из GUI

**Затронуто:** `main_qt.py` (`on_image_open_requested`)  
**Категория:** Local file safety / defense in depth

### Наблюдение

Перед `QDesktopServices.openUrl()` проверяется только существование пути, но нет явной проверки, что путь остается внутри ожидаемого каталога image-cache.

### Влияние

В текущем потоке путь контролируется приложением, поэтому риск низкий; однако при будущих изменениях логики это может стать точкой открытия произвольного локального файла.

### Рекомендации

1. Проверять `realpath(path)` относительно `realpath(get_images_dir())`.
2. Отказывать в открытии файлов вне разрешенной директории.

---

## [LOW] F-05: `_load_pixmap` содержит TOCTOU-шаблон `exists -> open`

**Затронуто:** `main_qt.py` (`_load_pixmap`)  
**Категория:** Local race hardening

### Наблюдение

Сначала проверяется `os.path.exists(path)`, затем выполняется загрузка `QPixmap(path)`.

### Влияние

При наличии локального атакующего процесса и конкурентной подмены файла возможен race с непредсказуемым поведением загрузки ресурса.

### Рекомендации

1. Убрать предварительный `exists` и обрабатывать только результат фактической загрузки.
2. Опционально добавить realpath confinement для image-cache.

---

## Что закрыто по сравнению с прошлым отчетом

Подтверждено как **исправленное/устаревшее**:
- прежний риск TOCTOU при импорте профиля в GUI: импорт выполняется через `import_profile_dat_atomic(...)` и покрыт тестом конкурентного импорта;
- прежний риск скачивания `appimagetool` по `latest` без проверки: в Linux build есть pin версии и SHA256 verification;
- прежний риск непинованных GitHub Actions: actions закреплены по commit SHA;
- прежний риск отсутствия `permissions` в workflows: задано `permissions: contents: read`;
- прежнее утверждение про отсутствие hash pinning зависимостей: `requirements.txt` уже hash-pinned.

## Подтвержденные сильные стороны

- MAC-проверка включает контекст `seq/msg_id/flags`; tampering режется до обработки payload.
- Защита от downgrade после handshake реализована и подтверждена тестами.
- Replay/out-of-order защита по ожидаемому sequence включена.
- ACK-подтверждения валидируются по контексту (peer/session/kind/state).
- Binding peer identity верифицируется через SAM lookup перед фиксацией identity.
- Импорт `.dat` профиля реализован атомарно и race-safe.
- CI-workflows уже harden-ы по action SHA pinning и минимальным permissions.

## Пробелы тестирования

Рекомендуемые дополнения:

1. GUI test для path confinement в `on_image_open_requested`.
2. GUI/unit test для сценария конкурентной подмены файла при `_load_pixmap`.
3. CI policy test на наличие pinned tooling (например, `pyinstaller`, `pip-audit`).
4. Проверка воспроизводимости release-сборки (artifact diff / build metadata).

## Приоритет remediation

1. **P1:** закрыть F-01 (hash-pinned build toolchain, включая `pyinstaller`).
2. **P2:** закрыть F-02 и F-03 (пинning CI tooling и hardening nix workflow reproducibility).
3. **P3:** закрыть F-04 и F-05 (GUI path/race hardening как defense in depth).

## Заключение

Безопасность протокольного ядра и CI-базиса на текущем состоянии проекта оценивается как заметно усиленная по сравнению с предыдущей ревизией. Оставшиеся риски носят преимущественно supply-chain и hardening-характер, без подтвержденных критических/высоких эксплуатационных уязвимостей в runtime-пути обмена сообщениями.
