# Docker: Linux build on Ubuntu 24.04 (glibc 2.39)

Use this image when you want **`build-linux.sh`** to run on **glibc 2.39** (Ubuntu 24.04 “noble”) instead of your host (e.g. Arch with a newer glibc) or instead of CI’s **ubuntu-22.04** (glibc 2.35).

**Trade-off:** artifacts from this image may require **a newer glibc** than 22.04 LTS users have. The default **GitHub Actions** workflow [`.github/workflows/build-linux-release-artifacts.yml`](../../.github/workflows/build-linux-release-artifacts.yml) stays on **ubuntu-22.04** for broad compatibility.

## Requirements

- **Docker** *with a running daemon* (`docker info` OK) **or** **Podman** (`podman info` OK)
- Network for `pip`, `wget` (appimagetool), and GitHub downloads used by `build-linux.sh`

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

**BuildKit deprecation** (legacy builder): the script sets `DOCKER_BUILDKIT=1` for Docker. Install Docker Buildx if your distro splits it out.

## Quick start

From the **repository root**:

```bash
./packaging/docker/run-linux-build.sh
```

This builds the image `i2pchat-linux:noble-glibc239` and runs `./build-linux.sh` with the tree bind-mounted at `/src`.

Outputs (`dist/`, `*.zip`, `I2PChat.AppDir`, etc.) appear in your working tree. If files are owned by `root`, fix with:

```bash
sudo chown -R "$(id -u):$(id -g)" dist build I2PChat.AppDir *.zip SHA256SUMS SHA256SUMS.asc 2>/dev/null || true
```

## Manual commands

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

## Python

The image uses **deadsnakes** `python3.14` on Ubuntu 24.04. If the PPA layout changes, adjust the Dockerfile while keeping **noble** as the base to retain glibc 2.39.
