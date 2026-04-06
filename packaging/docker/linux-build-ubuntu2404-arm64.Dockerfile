# Build environment for I2PChat Linux **aarch64** artifacts (AppImage + zips) on Ubuntu 24.04.
# Image: deps only; mount repo at /src and run ./build-linux.sh (see packaging/docker/README.md).
# Platform: pass `docker buildx build --platform linux/arm64` (no fixed FROM --platform — BuildKit lint).
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC

RUN apt-get update -qq \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    software-properties-common \
  && add-apt-repository -y ppa:deadsnakes/ppa \
  && apt-get update -qq \
  && apt-get install -y --no-install-recommends \
    python3.14 \
    python3.14-dev \
    python3.14-venv \
    wget \
    desktop-file-utils \
    file \
    patchelf \
    libfuse2t64 \
    fuse3 \
    libgl1 \
    libegl-mesa0 \
    libxkbcommon-x11-0 \
    libdbus-1-3 \
    libglib2.0-0t64 \
    libfontconfig1 \
    libfreetype6 \
    libdrm2 \
    libxcb1 \
    libxcb-xinerama0 \
    libxcb-xfixes0 \
    libxcb-shape0 \
    libxcb-render0 \
    libxcb-shm0 \
    libxcb-randr0 \
    libxcb-keysyms1 \
    libxcb-image0 \
    libxcb-icccm4 \
    libxcb-util1 \
    libxcb-sync1 \
    libxcb-xinput0 \
    libxcb-cursor0 \
    libcrypt1 \
    build-essential \
    zlib1g \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

WORKDIR /src
