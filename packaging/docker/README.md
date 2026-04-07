# Docker: Linux release builds

## x86_64 — Ubuntu 24.04 (glibc 2.39)

Use **`./packaging/docker/run-linux-build.sh`** from the repo root (Docker or Podman). It builds **`Dockerfile.linux-noble-glibc239`** and runs **`./build-linux.sh`** → **`I2PChat-linux-x86_64-v*.zip`** and related outputs.

### Troubleshooting: `failed to connect to the docker API` / `docker.sock`

The Docker **client** is installed but the **daemon** is not running (or your user cannot access `/var/run/docker.sock`).

**Arch / CachyOS (systemd):**

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out and back in, then retry
```

Check: `docker info` should print server info, not a socket error.

**Use Podman instead** (no Docker daemon):

```bash
sudo pacman -S podman
I2PCHAT_CONTAINER_RUNTIME=podman ./packaging/docker/run-linux-build.sh
```

Override explicitly: `I2PCHAT_CONTAINER_RUNTIME=docker` or `=podman`.

**BuildKit / buildx:** if you see *BuildKit is enabled but the buildx component is missing*, install **`docker-buildx`** (Arch / CachyOS: `sudo pacman -S docker-buildx`). With buildx present, **`run-linux-build.sh`** sets **`DOCKER_BUILDKIT=1`** automatically. To force BuildKit off: `I2PCHAT_DOCKER_BUILDKIT=0 ./packaging/docker/run-linux-build.sh`.

**`failed to export layer` / `device or resource busy` (containerd):** the Dockerfile step can succeed, but committing the next layer fails on some hosts (e.g. rolling distros). Install **`docker-buildx`** (Arch / CachyOS: `sudo pacman -S docker-buildx`); **`run-linux-build.sh`** then enables **BuildKit** automatically when `docker buildx` is available. Manual: `I2PCHAT_DOCKER_BUILDKIT=1 docker build ...`. To force the legacy builder: `I2PCHAT_DOCKER_BUILDKIT=0 ./packaging/docker/run-linux-build.sh`. Also try `sudo systemctl restart docker`, less concurrent container I/O, or **Podman** (`I2PCHAT_CONTAINER_RUNTIME=podman`).

### Quick start (x86_64)

From the **repository root**:

```bash
./packaging/docker/run-linux-build.sh
```

This builds the image `i2pchat-linux:noble-glibc239` and runs `./build-linux.sh` with the tree bind-mounted at `/src`.

Outputs (`dist/`, `*.zip`, `I2PChat.AppDir`, etc.) appear in your working tree. If files are owned by `root`, fix with:

```bash
sudo chown -R "$(id -u):$(id -g)" dist build I2PChat.AppDir *.zip SHA256SUMS SHA256SUMS.asc 2>/dev/null || true
```

### Manual commands (x86_64 image)

```bash
docker build -f packaging/docker/Dockerfile.linux-noble-glibc239 \
  -t i2pchat-linux:noble-glibc239 packaging/docker

docker run --rm -it \
  -e I2PCHAT_SKIP_GPG_SIGN=1 \
  -e QT_QPA_PLATFORM=offscreen \
  -e APPIMAGE_EXTRACT_AND_RUN=1 \
  -v "$PWD:/src:rw" \
  -w /src \
  i2pchat-linux:noble-glibc239 \
  ./build-linux.sh
```

### Python (noble x86_64 image)

The image uses **deadsnakes** stable + **nightly** PPAs: **Python 3.14** is not in the stable PPA for noble yet, so **`ppa:deadsnakes/nightly`** supplies `python3.14`. If packaging changes, adjust the Dockerfile while keeping **noble** as the base to retain glibc 2.39.

## aarch64 (arm64) — Ubuntu 24.04

**`linux-build-ubuntu2404-arm64.Dockerfile`** — Ubuntu **24.04** on **linux/arm64** with Python **3.14** (deadsnakes), Qt/AppImage dependencies.

## Quick run (aarch64)

From the **repository root**:

```bash
./packaging/docker/build-linux-aarch64.sh
```

The script builds the image and runs **`./build-linux.sh`** end-to-end (GUI AppImage + zip, затем TUI zip). Репозиторий смонтирован в `/src`. Оба релизных zip — **в корне репо** (`I2PChat-linux-aarch64-v*.zip`, `*-tui-*`); в **`dist/`** — AppImage и каталоги PyInstaller.

## Prerequisites

1. **Docker Buildx** and an arm64-capable builder. On Apple Silicon, native arm64 is fine. On x86_64 Linux/macOS you typically need QEMU/binfmt (e.g. `docker run --privileged --rm tonistiigi/binfmt --install all` once, then a `docker buildx` builder using the `docker-container` driver).

2. Optional bundled `i2pd` for portable builds: with an empty `vendor/i2pd/`, `build-linux.sh` runs `ensure_bundled_i2pd.sh`, which by default clones **[MetanoicArmor/i2pchat-bundled-i2pd](https://github.com/MetanoicArmor/i2pchat-bundled-i2pd)** into `.cache/` (needs network in the container). Set **`I2PCHAT_SKIP_BUNDLED_I2PD_GIT=1`** to skip. If clone fails or is skipped, artifacts build without an embedded router.

3. Same **uv** lockfile as a normal local build (`pyproject.toml`, `uv.lock`); the image includes the `uv` binary for `build-linux.sh`.

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
