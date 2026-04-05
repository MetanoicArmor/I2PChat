Param(
    [string]$VenvDir = ".venv314"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$BlindboxInstallSrc = "i2pchat\blindbox\daemon\install\install.sh"

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

function Stop-I2PChatProcessesLockingDist {
    # Запущенный dist\I2PChat\*.exe держит _sodium.pyd — Remove-Item падает с PermissionDenied.
    foreach ($procName in @("I2PChat", "I2PChat-tui")) {
        Get-Process -Name $procName -ErrorAction SilentlyContinue | ForEach-Object {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
}

function Remove-PathWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Attempts = 6
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $delayMs = 250
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if ($i -eq $Attempts - 1) {
                throw "Cannot remove '$Path' after $Attempts attempts: $($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds $delayMs
            $delayMs = [Math]::Min(2000, $delayMs + 250)
        }
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

Write-Host "==> Syntax check (packages + helper scripts, same scope as Linux/macOS build)"
Invoke-NativeChecked $PythonExe @("-m", "compileall", "i2pchat", "vendor/i2plib", "scripts", "make_icon.py")

Write-Host "==> Build GUI I2PChat.exe using spec file"
Stop-I2PChatProcessesLockingDist
Remove-PathWithRetry -Path "dist\I2PChat"
Remove-PathWithRetry -Path "build\I2PChat"
Invoke-NativeChecked $PythonExe @("-m", "PyInstaller", "--clean", "-y", "I2PChat.spec")

if (Test-Path "vendor\i2pd\windows-x64\i2pd.exe") {
    $BundledRouterDir = "dist\I2PChat\vendor\i2pd\windows-x64"
    New-Item -ItemType Directory -Force -Path $BundledRouterDir | Out-Null
    Copy-Item "vendor\i2pd\windows-x64\i2pd.exe" "$BundledRouterDir\i2pd.exe"
    Get-ChildItem "vendor\i2pd\windows-x64\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName $BundledRouterDir
    }
}

Write-Host ""
Write-Host "Done."
Write-Host "GUI binary: dist\\I2PChat\\I2PChat.exe"
Write-Host "TUI binary (console): dist\\I2PChat\\I2PChat-tui.exe"
Write-Host "Security profile: signed handshake + TOFU (release $ReleaseVersion)"

$ZipFile = "dist\\I2PChat-windows-x64-v$ReleaseVersion.zip"
if (Test-Path $ZipFile) {
    Remove-Item -Force $ZipFile
}
$ZipStage = "dist\\I2PChat-windows-x64-v$ReleaseVersion"
if (Test-Path $ZipStage) {
    Remove-Item -Recurse -Force $ZipStage
}
New-Item -ItemType Directory -Path $ZipStage | Out-Null
Copy-Item -Recurse "dist\\I2PChat" "$ZipStage\\I2PChat"
if (Test-Path $BlindboxInstallSrc) {
    Copy-Item $BlindboxInstallSrc "$ZipStage\\install.sh"
}
Compress-Archive -Path "$ZipStage\\*" -DestinationPath $ZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $ZipStage
Write-Host "Packed: $ZipFile"

# TUI-only zip: same layout (I2PChat/…) but without GUI exe — for winget/Homebrew-style -tui packages.
$TuiZipFile = "dist\\I2PChat-windows-tui-x64-v$ReleaseVersion.zip"
if (Test-Path $TuiZipFile) {
    Remove-Item -Force $TuiZipFile
}
$TuiStage = "dist\\I2PChat-windows-tui-x64-v$ReleaseVersion"
if (Test-Path $TuiStage) {
    Remove-Item -Recurse -Force $TuiStage
}
New-Item -ItemType Directory -Path "$TuiStage\\I2PChat" | Out-Null
Copy-Item "dist\\I2PChat\\I2PChat-tui.exe" "$TuiStage\\I2PChat\\"
Copy-Item -Recurse "dist\\I2PChat\\_internal" "$TuiStage\\I2PChat\\_internal"
if (Test-Path "dist\\I2PChat\\vendor") {
    Copy-Item -Recurse "dist\\I2PChat\\vendor" "$TuiStage\\I2PChat\\vendor"
}
if (Test-Path $BlindboxInstallSrc) {
    Copy-Item $BlindboxInstallSrc "$TuiStage\\install.sh"
}
Compress-Archive -Path "$TuiStage\\*" -DestinationPath $TuiZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $TuiStage
Write-Host "Packed (TUI only): $TuiZipFile"

$sumGui = (Get-FileHash -Path $ZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumTui = (Get-FileHash -Path $TuiZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$nameGui = Split-Path -Path $ZipFile -Leaf
$nameTui = Split-Path -Path $TuiZipFile -Leaf
Set-Content -Path "SHA256SUMS" -Encoding utf8 -Value @(
    "$sumGui  $nameGui",
    "$sumTui  $nameTui"
)
Write-Host "Generated: SHA256SUMS (GUI + TUI zips)"

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
