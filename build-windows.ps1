Param(
    [string]$VenvDir = ".venv",
    [string]$AppName = "termchat-i2p"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "==> Создаю/обновляю виртуальное окружение $VenvDir (Python 3.9)"
if (-not (Test-Path $VenvDir)) {
    py -3.9 -m venv $VenvDir
}

Write-Host "==> Активирую виртуальное окружение"
& "$VenvDir\Scripts\Activate.ps1"

Write-Host "==> Устанавливаю зависимости из requirements.txt"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

Write-Host "==> Собираю одиночный бинарник PyInstaller'ом для Windows"
pyinstaller --clean --onefile --name $AppName chat-python.py

Write-Host ""
Write-Host "✔ Бинарник собран: dist\$AppName.exe"
Write-Host "Можешь копировать dist\$AppName.exe на другие Windows-машины и запускать двойным кликом или из PowerShell/cmd."

