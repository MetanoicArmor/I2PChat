# Docker: Linux release builds

## x86_64 — Ubuntu 24.04 (glibc 2.39)

Use **`./packaging/docker/run-linux-build.sh`** from the repo root (Docker or Podman). It builds **`Dockerfile.linux-noble-glibc239`** and runs **`./build-linux.sh`** → **`I2PChat-linux-x86_64-v*.zip`** and related outputs.

## aarch64 (arm64) — Ubuntu 24.04

**`linux-build-ubuntu2404-arm64.Dockerfile`** — Ubuntu **24.04** on **linux/arm64** with Python **3.14** (deadsnakes), Qt/AppImage dependencies.

## Quick run

From the **repository root**:

```bash
./packaging/docker/build-linux-aarch64.sh
```

The script builds the image and runs **`./build-linux.sh`** end-to-end (GUI AppImage + zip, затем TUI zip). Репозиторий смонтирован в `/src`. Оба релизных zip — **в корне репо** (`I2PChat-linux-aarch64-v*.zip`, `*-tui-*`); в **`dist/`** — AppImage и каталоги PyInstaller.

## Prerequisites

1. **Docker Buildx** and an arm64-capable builder. On Apple Silicon, native arm64 is fine. On x86_64 Linux/macOS you typically need QEMU/binfmt (e.g. `docker run --privileged --rm tonistiigi/binfmt --install all` once, then a `docker buildx` builder using the `docker-container` driver).

2. **`vendor/i2pd/linux-aarch64/i2pd`** — executable i2pd for aarch64 (see [`../../vendor/i2pd/linux-aarch64/README.md`](../../vendor/i2pd/linux-aarch64/README.md)). Without it, the script exits with an error before build.

3. Same Python **hashed** requirements as a normal local build (`requirements.txt`, `requirements-build.txt`).

## Manual image build

```bash
docker buildx build --platform linux/arm64 --load \
  -f packaging/docker/linux-build-ubuntu2404-arm64.Dockerfile \
  -t i2pchat-linux-build:ubuntu-24.04-arm64 \
  packaging/docker
```

## Environment overrides

| Variable | Default | Meaning |
|----------|---------|---------|
| `I2PCHAT_LINUX_ARM64_IMAGE` | `i2pchat-linux-build:ubuntu-24.04-arm64` | Image tag for `build-linux-aarch64.sh` |

Inside the container, `build-linux.sh` respects **`I2PCHAT_SKIP_GPG_SIGN`**, **`APPIMAGE_EXTRACT_AND_RUN`**, **`QT_QPA_PLATFORM`** (set by the script).

## Where outputs go (host)

Скрипты монтируют **весь репозиторий** в контейнер как **`/src` с записью** (`-v "$ROOT:/src:rw"`). Сборки **сразу оказываются на вашей машине** в том же клоне: `dist/`, `I2PChat.AppImage`, `I2PChat-linux-<arch>-v*.zip`, `*-tui-*.zip`, `SHA256SUMS`. После успешной сборки скрипт печатает абсолютные пути.

**Если запускали контейнер вручную без монтирования** и нужно вытащить файлы:

```bash
CID=$(docker create i2pchat-linux-build:ubuntu-24.04-arm64)   # или ваш тег
docker cp "${CID}:/src/I2PChat-linux-aarch64-v1.2.3.zip" .
docker cp "${CID}:/src/dist" ./dist-from-docker
docker rm "${CID}"
```

Подставьте фактическую версию из `VERSION` и имена файлов из вывода `build-linux.sh`.
