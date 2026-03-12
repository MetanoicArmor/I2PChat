#!/usr/bin/env bash

set -e

APP_NAME="termchat-i2p"
VENV_DIR=".venv39"

echo "==> Создаю/обновляю виртуальное окружение ${VENV_DIR} (Python 3.9)"
if ! command -v python3.9 >/dev/null 2>&1; then
  echo "Требуется python3.9 (i2plib не совместим с 3.14+)."
  echo "Установите python3.9 (через пакеты или pyenv) и повторите."
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  python3.9 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

echo "==> Устанавливаю зависимости из requirements.txt"
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo "==> Собираю одиночный бинарник PyInstaller'ом для Linux"
pyinstaller --clean --onefile --name "${APP_NAME}" chat-python.py

echo
echo "✔ Бинарник собран: dist/${APP_NAME}"
echo "Скопируй его на нужную Linux-машину и запускай как обычный CLI-инструмент."

