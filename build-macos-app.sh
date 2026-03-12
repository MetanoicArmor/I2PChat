#!/usr/bin/env bash

set -e

APP_NAME="TermChat I2P"
VENV_DIR=".venv314"

echo "==> Создаю/обновляю виртуальное окружение ${VENV_DIR} (Python 3.14)"
if command -v python3.14 &>/dev/null; then
  PY_BIN="python3.14"
else
  PY_BIN="python3"
fi

if [ ! -d "${VENV_DIR}" ]; then
  "${PY_BIN}" -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

echo "==> Устанавливаю зависимости из requirements.txt"
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

echo "==> Собираю одиночный бинарник PyInstaller'ом"
pyinstaller --clean --onefile --name termchat-i2p chat-python.py

echo
echo "✔ Бинарник собран: dist/termchat-i2p"
echo
echo "Дальше сделай вручную в Platypus (GUI):"
echo "  1) Запусти Platypus и создай новый проект."
echo "  2) Script Type: Binary."
echo "  3) Script: выбери dist/termchat-i2p."
echo "  4) Interface: Text Window."
echo "  5) Icon: можешь указать chat.png."
echo "  6) Name: ${APP_NAME}."
echo "  7) Нажми «Create App» и сохрани, например, как dist/${APP_NAME}.app."
echo
echo "После этого ты сможешь запускать ${APP_NAME}.app обычным двойным кликом."

