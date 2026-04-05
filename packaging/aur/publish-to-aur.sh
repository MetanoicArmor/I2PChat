#!/usr/bin/env bash
# Синхронизация PKGBUILD + .SRCINFO из этого репозитория в Git AUR.
# Запускайте в своём терминале (нужен доступ к SSH-ключу: ssh-add или агент сессии).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONOREPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKDIR="${TMPDIR:-/tmp}/aur-i2pchat-publish-$$"
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o IdentitiesOnly=yes -i ${HOME}/.ssh/aur_ed25519}"

PACKAGES=(i2pchat-bin i2pchat-tui-bin)

echo "==> Monorepo: ${MONOREPO}"
echo "==> Workdir:  ${WORKDIR}"
echo "==> SSH:      ${GIT_SSH_COMMAND}"
echo ""
echo "Если увидите «Permission denied (publickey)», сначала:"
echo "  ssh-add ~/.ssh/aur_ed25519"
echo ""

cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

mkdir -p "${WORKDIR}"

for pkg in "${PACKAGES[@]}"; do
  src="${MONOREPO}/packaging/aur/${pkg}"
  if [ ! -f "${src}/PKGBUILD" ] || [ ! -f "${src}/.SRCINFO" ]; then
    echo "ERROR: нет ${src}/PKGBUILD или .SRCINFO" >&2
    exit 1
  fi

  pkgver="$(grep -m1 '^pkgver=' "${src}/PKGBUILD" | cut -d= -f2 | tr -d "'\"[:space:]")"
  pkgrel="$(grep -m1 '^pkgrel=' "${src}/PKGBUILD" | cut -d= -f2 | tr -d "'\"[:space:]")"

  echo "==> ${pkg} (${pkgver}-${pkgrel})"
  clone="${WORKDIR}/${pkg}"
  rm -rf "${clone}"
  git clone "ssh://aur@aur.archlinux.org/${pkg}.git" "${clone}"
  cd "${clone}"

  if git rev-parse --verify HEAD >/dev/null 2>&1; then
    git pull --rebase origin master 2>/dev/null || true
  fi

  cp "${src}/PKGBUILD" "${src}/.SRCINFO" .

  git add PKGBUILD .SRCINFO
  if git diff --cached --quiet; then
    echo "    (без изменений, push не нужен)"
  else
    git commit -m "${pkg} ${pkgver}-${pkgrel} (sync from I2PChat packaging)"
  fi

  # AUR использует ветку master (-u для первого push из пустого клона)
  git push -u origin master
  echo "    ✔ ${pkg}"
done

echo ""
echo "Готово. Проверьте: https://aur.archlinux.org/packages?K=i2pchat"
