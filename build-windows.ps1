Param(
    [string]$VenvDir = ".venv314"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter()][string[]]$Arguments = @()
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        $argsText = if ($Arguments.Count -gt 0) { " " + ($Arguments -join " ") } else { "" }
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath$argsText"
    }
}
$VersionFile = "VERSION"
if (-not (Test-Path $VersionFile)) {
    throw "VERSION file not found: $VersionFile"
}
$ReleaseVersion = (Get-Content -Path $VersionFile -Raw).Trim()
if (-not $ReleaseVersion) {
    throw "VERSION file is empty: $VersionFile"
}

# Используем установленный Python 3.14+, с fallback на latest Python 3
$PyLauncherArgs = @("-3.14")
& py @PyLauncherArgs -c "import sys; print(sys.version)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    $PyLauncherArgs = @("-3")
}

Write-Host "==> Create fresh virtual environment $VenvDir (Python 3.14+ preferred)"
if (Test-Path $VenvDir) {
    Remove-Item -Recurse -Force $VenvDir
}
& py @PyLauncherArgs -m venv $VenvDir

Write-Host "==> Activate virtual environment"
& "$VenvDir\Scripts\Activate.ps1"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Virtualenv python not found: $PythonExe"
}

Write-Host "==> Install dependencies from requirements.txt"
Invoke-NativeChecked $PythonExe @("-m", "pip", "install", "--upgrade", "pip")
Invoke-NativeChecked $PythonExe @("-m", "pip", "install", "--require-hashes", "-r", "requirements.txt")
Invoke-NativeChecked $PythonExe @("-m", "pip", "install", "--require-hashes", "-r", "requirements-build.txt")

Write-Host "==> Check PyNaCl (required for secure protocol)"
Invoke-NativeChecked $PythonExe @("-c", "import nacl; from nacl.secret import SecretBox")

Write-Host "==> Compile security-critical modules"
Invoke-NativeChecked $PythonExe @("-m", "compileall", "i2p_chat_core.py", "crypto.py", "main_qt.py")

Write-Host "==> Build GUI I2PChat.exe using spec file"
if (Test-Path "dist\I2PChat") { Remove-Item -Recurse -Force "dist\I2PChat" }
if (Test-Path "build\I2PChat") { Remove-Item -Recurse -Force "build\I2PChat" }
Invoke-NativeChecked $PythonExe @("-m", "PyInstaller", "--clean", "-y", "I2PChat.spec")

Write-Host ""
Write-Host "Done."
Write-Host "GUI binary: dist\\I2PChat\\I2PChat.exe"
Write-Host "Security profile: signed handshake + TOFU (release $ReleaseVersion)"

$ZipFile = "dist\\I2PChat-windows-x64-v$ReleaseVersion.zip"
if (Test-Path $ZipFile) {
    Remove-Item -Force $ZipFile
}
Compress-Archive -Path "dist\\I2PChat" -DestinationPath $ZipFile -CompressionLevel Optimal
Write-Host "Packed: $ZipFile"

$HashLine = "{0}  {1}" -f (Get-FileHash -Path $ZipFile -Algorithm SHA256).Hash.ToLowerInvariant(), (Split-Path -Path $ZipFile -Leaf)
Set-Content -Path "SHA256SUMS" -Value $HashLine -NoNewline -Encoding utf8
Write-Host "Generated: SHA256SUMS"

if ($env:I2PCHAT_SKIP_GPG_SIGN -eq "1") {
    Write-Warning "Skipping GPG detached signature (I2PCHAT_SKIP_GPG_SIGN=1)"
}
elseif (-not (Get-Command gpg -ErrorAction SilentlyContinue)) {
    if ($env:I2PCHAT_REQUIRE_GPG -eq "1") {
        throw "gpg is required to create detached release signature"
    }
    Write-Warning "gpg not found; skipping detached signature (set I2PCHAT_REQUIRE_GPG=1 to enforce)"
}
else {
    $GpgArgs = @("--batch", "--yes", "--armor", "--detach-sign", "--output", "SHA256SUMS.asc")
    if ($env:I2PCHAT_GPG_KEY_ID) {
        $GpgArgs += @("--local-user", $env:I2PCHAT_GPG_KEY_ID)
    }
    $GpgArgs += "SHA256SUMS"
    & gpg @GpgArgs
    if ($LASTEXITCODE -ne 0) {
        if ($env:I2PCHAT_REQUIRE_GPG -eq "1") {
            throw "gpg failed with exit code $LASTEXITCODE in required mode"
        }
        Write-Warning "gpg signing failed; continuing without detached signature"
    }
    else {
        Write-Host "Generated: SHA256SUMS.asc"
    }
}

