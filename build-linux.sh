#!/usr/bin/env bash

set -e

APP_NAME="termchat-i2p"
VENV_DIR=".venv"

echo "==> Создаю/обновляю виртуальное окружение ${VENV_DIR} (Python 3.9)"
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

