#!/usr/bin/env bash
# Print SHA256 for GitHub release zips + icon.png for a given tag (default: latest on GitHub).
# Use when bumping Homebrew, winget, AUR, or deb packaging after a release.
set -euo pipefail

TAG="${1:-}"

if [[ -z "$TAG" ]]; then
  TAG="$(curl -fsSL "https://api.github.com/repos/MetanoicArmor/I2PChat/releases/latest" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))")"
fi

TAG="${TAG#v}"
if [[ -z "$TAG" ]]; then
  echo "ERROR: could not resolve release tag" >&2
  exit 1
fi

BASE="https://github.com/MetanoicArmor/I2PChat/releases/download/v${TAG}"
MAC_ZIP="I2PChat-macOS-arm64-v${TAG}.zip"
MAC_TUI_ZIP="I2PChat-macos-arm64-tui-v${TAG}.zip"
WIN_TUI_ZIP="I2PChat-windows-tui-x64-v${TAG}.zip"
WIN_GUI_WINGET_ZIP="I2PChat-windows-x64-winget-v${TAG}.zip"
WIN_TUI_WINGET_ZIP="I2PChat-windows-tui-x64-winget-v${TAG}.zip"
LINUX_TUI_ZIP="I2PChat-linux-x86_64-tui-v${TAG}.zip"
declare -a FILES=(
  "${MAC_ZIP}"
  "${MAC_TUI_ZIP}"
  "I2PChat-windows-x64-v${TAG}.zip"
  "${WIN_TUI_ZIP}"
  "${WIN_GUI_WINGET_ZIP}"
  "${WIN_TUI_WINGET_ZIP}"
  "I2PChat-linux-x86_64-v${TAG}.zip"
  "${LINUX_TUI_ZIP}"
)

echo "# Release v${TAG}"
echo "# --- SHA256 (paste into packaging files) ---"
MAC_SUM=""
MAC_TUI_SUM=""
WIN_TUI_SUM=""
WIN_GUI_WINGET_SUM=""
WIN_TUI_WINGET_SUM=""
for f in "${FILES[@]}"; do
  url="${BASE}/${f}"
  sum="$(curl -fsSL "$url" | sha256sum | awk '{print $1}')" || {
    echo "ERROR: failed to fetch or hash $url" >&2
    exit 1
  }
  printf '%s  %s\n' "$sum" "$f"
  if [[ "$f" == "$MAC_ZIP" ]]; then
    MAC_SUM="$sum"
  fi
  if [[ "$f" == "$MAC_TUI_ZIP" ]]; then
    MAC_TUI_SUM="$sum"
  fi
  if [[ "$f" == "$WIN_TUI_ZIP" ]]; then
    WIN_TUI_SUM="$sum"
  fi
  if [[ "$f" == "$WIN_GUI_WINGET_ZIP" ]]; then
    WIN_GUI_WINGET_SUM="$sum"
  fi
  if [[ "$f" == "$WIN_TUI_WINGET_ZIP" ]]; then
    WIN_TUI_WINGET_SUM="$sum"
  fi
done

icon_url="https://github.com/MetanoicArmor/I2PChat/raw/v${TAG}/icon.png"
icon_sum="$(curl -fsSL "$icon_url" | sha256sum | awk '{print $1}')"
printf '%s  icon.png (raw v%s)\n' "$icon_sum" "$TAG"

echo "# --- Homebrew cask i2pchat (mac GUI zip) ---"
echo "  version \"${TAG}\""
echo "  sha256 \"${MAC_SUM}\""
echo "# --- Homebrew cask i2pchat-tui (mac TUI zip) ---"
echo "  version \"${TAG}\""
echo "  sha256 \"${MAC_TUI_SUM}\""
echo "# --- winget MetanoicArmor.I2PChat (windows GUI *-winget-* zip, no embedded i2pd) ---"
echo "  InstallerUrl: .../${WIN_GUI_WINGET_ZIP}"
echo "  InstallerSha256: ${WIN_GUI_WINGET_SUM}"
echo "# --- winget MetanoicArmor.I2PChat.TUI (*-winget-* zip) ---"
echo "  InstallerUrl: .../${WIN_TUI_WINGET_ZIP}"
echo "  InstallerSha256: ${WIN_TUI_WINGET_SUM}"
echo "# --- (full Windows TUI zip with embedded i2pd — not for winget manifest) ---"
echo "  ${WIN_TUI_SUM}  ${WIN_TUI_ZIP}"
