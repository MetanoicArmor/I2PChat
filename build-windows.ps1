Param(
    [string]$VenvDir = ".venv39"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# строго Python 3.9, как требовалось автором (i2plib и остальное тестировалось на нём)
Write-Host "==> Create fresh virtual environment $VenvDir (Python 3.9)"
if (Test-Path $VenvDir) {
    Remove-Item -Recurse -Force $VenvDir
}
py -3.9 -m venv $VenvDir

Write-Host "==> Activate virtual environment"
& "$VenvDir\Scripts\Activate.ps1"

Write-Host "==> Install dependencies from requirements.txt"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

Write-Host "==> Build GUI I2PChat.exe with icon"
if (Test-Path "dist\I2PChat") { Remove-Item -Recurse -Force "dist\I2PChat" }
pyinstaller --clean -y --noconsole --name I2PChat --icon icon-1024.png main_qt.py

Write-Host ""
Write-Host "Done."
Write-Host "GUI binary: dist\\I2PChat\\I2PChat.exe"

