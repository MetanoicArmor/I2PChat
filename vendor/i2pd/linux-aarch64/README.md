# Bundled i2pd (Linux aarch64)

Place a **static or distro-built `i2pd`** binary for **64-bit ARM (aarch64)** here, named **`i2pd`**, and `chmod +x`.

Same role as `vendor/i2pd/linux-x86_64/i2pd` on x86_64 builds. Without this file, PyInstaller still builds, but the **embedded router** will be missing until you add a binary or use a system i2pd in the app.

Upstream: [PurpleI2P/i2pd releases](https://github.com/PurpleI2P/i2pd/releases) (pick an aarch64 / arm64 Linux asset, or compile from source on arm64).
