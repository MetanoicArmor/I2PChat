Param(
    [string]$VenvDir = ".venv314"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ReleaseVersion = "0.3.0"

# Используем установленный Python 3.14+, с fallback на latest Python 3
$PyLauncherArgs = @("-3.14")
try {
    & py @PyLauncherArgs -c "import sys; print(sys.version)" | Out-Null
}
catch {
    $PyLauncherArgs = @("-3")
}

Write-Host "==> Create fresh virtual environment $VenvDir (Python 3.14+ preferred)"
if (Test-Path $VenvDir) {
    Remove-Item -Recurse -Force $VenvDir
}
& py @PyLauncherArgs -m venv $VenvDir

Write-Host "==> Activate virtual environment"
& "$VenvDir\Scripts\Activate.ps1"

Write-Host "==> Install dependencies from requirements.txt"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

Write-Host "==> Check PyNaCl (required for secure protocol)"
python -c "import nacl; from nacl.secret import SecretBox"

Write-Host "==> Compile security-critical modules"
python -m compileall i2p_chat_core.py crypto.py main_qt.py

Write-Host "==> Build GUI I2PChat.exe using spec file"
if (Test-Path "dist\I2PChat") { Remove-Item -Recurse -Force "dist\I2PChat" }
if (Test-Path "build\I2PChat") { Remove-Item -Recurse -Force "build\I2PChat" }
pyinstaller --clean -y I2PChat.spec

Write-Host ""
Write-Host "Done."
Write-Host "GUI binary: dist\\I2PChat\\I2PChat.exe"
Write-Host "Security profile: signed handshake + TOFU (release $ReleaseVersion)"

