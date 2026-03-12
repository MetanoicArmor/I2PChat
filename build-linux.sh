#!/usr/bin/env bash

set -e

APP_NAME="termchat-i2p"
VENV_DIR=".venv314"

echo "==> Создаю/обновляю виртуальное окружение ${VENV_DIR} (Python 3.14)"
if command -v python3.14 >/dev/null 2>&1; then
  PYTHON_BIN="python3.14"
else
  PYTHON_BIN="python3"
fi

if [ ! -d "${VENV_DIR}" ]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
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

