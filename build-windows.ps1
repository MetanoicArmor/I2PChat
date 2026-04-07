Param(
    [string]$VenvDir = ".venv"
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

function Get-I2PChatGpgExecutable {
    if ($env:I2PCHAT_GPG_EXE) {
        if (Test-Path -LiteralPath $env:I2PCHAT_GPG_EXE) {
            return (Resolve-Path -LiteralPath $env:I2PCHAT_GPG_EXE).Path
        }
        return $null
    }
    $fromPath = Get-Command gpg -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    $pf86 = ${env:ProgramFiles(x86)}
    foreach ($dir in @(
            (Join-Path $env:ProgramFiles "GnuPG\bin"),
            (Join-Path $pf86 "GnuPG\bin"),
            (Join-Path $env:LOCALAPPDATA "Programs\GnuPG\bin")
        )) {
        $exe = Join-Path $dir "gpg.exe"
        if (Test-Path -LiteralPath $exe) {
            return (Resolve-Path -LiteralPath $exe).Path
        }
    }
    return $null
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

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install: https://docs.astral.sh/uv/getting-started/installation/ (e.g. irm https://astral.sh/uv/install.ps1 | iex)"
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot
$env:UV_PROJECT_ENVIRONMENT = Join-Path $RepoRoot $VenvDir

function Copy-BundledI2pdFromSource {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir
    )
    if (-not (Test-Path -LiteralPath $SourceDir)) {
        return $false
    }
    $pairs = @(
        @{ Src = (Join-Path $SourceDir "windows-x64\i2pd.exe"); Dst = "vendor\i2pd\windows-x64\i2pd.exe" }
    )
    $copied = $false
    foreach ($pair in $pairs) {
        if (Test-Path -LiteralPath $pair.Src) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pair.Dst) | Out-Null
            Copy-Item $pair.Src $pair.Dst -Force
            $copied = $true
        }
    }
    return $copied
}

if ($env:I2PCHAT_OMIT_BUNDLED_I2PD -ne "1" -and -not (Test-Path "vendor\i2pd\windows-x64\i2pd.exe")) {
    Write-Host "==> Checking optional bundled Windows i2pd source"
    if ($env:I2PCHAT_BUNDLED_I2PD_SOURCE_DIR) {
        if (Copy-BundledI2pdFromSource -SourceDir $env:I2PCHAT_BUNDLED_I2PD_SOURCE_DIR) {
            Write-Host "==> Bundled i2pd: STAGED from I2PCHAT_BUNDLED_I2PD_SOURCE_DIR=$($env:I2PCHAT_BUNDLED_I2PD_SOURCE_DIR)"
        }
    }
    elseif (Test-Path "..\i2pchat-bundled-i2pd") {
        if (Copy-BundledI2pdFromSource -SourceDir "..\i2pchat-bundled-i2pd") {
            Write-Host "==> Bundled i2pd: STAGED from sibling repo ..\\i2pchat-bundled-i2pd"
        }
    }
    elseif ($env:I2PCHAT_SKIP_BUNDLED_I2PD_GIT -ne "1") {
        $procGitUrl = [Environment]::GetEnvironmentVariable("I2PCHAT_BUNDLED_I2PD_GIT_URL", "Process")
        $defaultBundledGit = "https://github.com/MetanoicArmor/i2pchat-bundled-i2pd.git"
        $gitUrlToUse = $null
        if ($null -eq $procGitUrl) {
            $gitUrlToUse = $defaultBundledGit
        }
        elseif ($procGitUrl -ne "") {
            $gitUrlToUse = $procGitUrl
        }
        if ($null -ne $gitUrlToUse) {
            $gitExe = Get-Command git -ErrorAction SilentlyContinue
            if ($gitExe) {
                $cacheDir = Join-Path $RepoRoot ".cache\bundled-i2pd-source"
                $parentCache = Split-Path -Parent $cacheDir
                if (-not (Test-Path -LiteralPath $parentCache)) {
                    New-Item -ItemType Directory -Force -Path $parentCache | Out-Null
                }
                if (-not (Test-Path -LiteralPath (Join-Path $cacheDir ".git"))) {
                    Remove-Item -Recurse -Force -Path $cacheDir -ErrorAction SilentlyContinue
                    & git clone --depth=1 $gitUrlToUse $cacheDir 2>$null
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "==> Bundled i2pd: git clone failed; building without embedded router (see https://github.com/MetanoicArmor/i2pchat-bundled-i2pd )"
                    }
                }
                else {
                    Push-Location $cacheDir
                    try {
                        & git pull --ff-only 2>$null | Out-Null
                    }
                    finally {
                        Pop-Location
                    }
                }
                if (Copy-BundledI2pdFromSource -SourceDir $cacheDir) {
                    Write-Host "==> Bundled i2pd: STAGED from git $gitUrlToUse"
                }
            }
            else {
                Write-Host "==> Bundled i2pd: git not on PATH; cannot clone $gitUrlToUse"
            }
        }
    }
    if (-not (Test-Path "vendor\i2pd\windows-x64\i2pd.exe")) {
        Write-Host "==> Bundled i2pd: NOT FOUND; building without embedded router (optional: https://github.com/MetanoicArmor/i2pchat-bundled-i2pd )"
    }
}

Write-Host "==> uv sync (locked runtime + build group, no dev tools)"
$uvPy = if ($PyLauncherArgs -contains "-3.14") { "3.14" } else { "3" }
Invoke-NativeChecked "uv" @("sync", "--frozen", "--python", $uvPy, "--group", "build", "--no-dev")

$PythonExe = Join-Path $RepoRoot (Join-Path $VenvDir "Scripts\python.exe")
if (-not (Test-Path $PythonExe)) {
    throw "Virtualenv python not found after uv sync: $PythonExe"
}

Write-Host "==> Check PyNaCl (required for secure protocol)"
Invoke-NativeChecked $PythonExe @("-c", "import nacl; from nacl.secret import SecretBox")

Write-Host "==> Syntax check (packages + helper scripts, same scope as Linux/macOS build)"
Invoke-NativeChecked $PythonExe @("-m", "compileall", "i2pchat", "scripts", "make_icon.py")

Write-Host "==> Build GUI I2PChat.exe using spec file"
Stop-I2PChatProcessesLockingDist
Remove-PathWithRetry -Path "dist\I2PChat"
Remove-PathWithRetry -Path "build\I2PChat"
Invoke-NativeChecked $PythonExe @("-m", "PyInstaller", "--clean", "-y", "I2PChat.spec")

if ($env:I2PCHAT_OMIT_BUNDLED_I2PD -ne "1") {
    if (Test-Path "vendor\i2pd\windows-x64\i2pd.exe") {
        $BundledRouterDir = "dist\I2PChat\vendor\i2pd\windows-x64"
        New-Item -ItemType Directory -Force -Path $BundledRouterDir | Out-Null
        Copy-Item "vendor\i2pd\windows-x64\i2pd.exe" "$BundledRouterDir\i2pd.exe"
        Get-ChildItem "vendor\i2pd\windows-x64\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName $BundledRouterDir
        }
    }
    else {
        Write-Host "==> No local bundled Windows i2pd found (build should clone https://github.com/MetanoicArmor/i2pchat-bundled-i2pd when git is on PATH; else fetch_bundled_i2pd.sh / manual vendor\\i2pd)"
    }
}

Write-Host "==> Build slim TUI-only bundle (I2PChat-tui.spec, no PyQt6)"
Stop-I2PChatProcessesLockingDist
Remove-PathWithRetry -Path "dist\I2PChat-tui"
Remove-PathWithRetry -Path "build\I2PChat-tui"
Invoke-NativeChecked $PythonExe @("-m", "PyInstaller", "--clean", "-y", "I2PChat-tui.spec")

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
Copy-Item "dist\\I2PChat-tui\\I2PChat-tui.exe" "$TuiStage\\I2PChat\\"
Copy-Item -Recurse "dist\\I2PChat-tui\\_internal" "$TuiStage\\I2PChat\\_internal"
if (Test-Path "dist\\I2PChat-tui\\vendor") {
    Copy-Item -Recurse "dist\\I2PChat-tui\\vendor" "$TuiStage\\I2PChat\\vendor"
}
if (Test-Path $BlindboxInstallSrc) {
    Copy-Item $BlindboxInstallSrc "$TuiStage\\install.sh"
}
Compress-Archive -Path "$TuiStage\\*" -DestinationPath $TuiZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $TuiStage
Write-Host "Packed (TUI only): $TuiZipFile"

# Second pass: PyInstaller without embedded i2pd — zips for winget / Microsoft validation (no Riskware.I2PD.A).
Write-Host ""
Write-Host '==> Rebuild for winget (I2PCHAT_OMIT_BUNDLED_I2PD=1, no embedded i2pd)'
Stop-I2PChatProcessesLockingDist
Remove-PathWithRetry -Path "dist\I2PChat"
Remove-PathWithRetry -Path "dist\I2PChat-tui"
Remove-PathWithRetry -Path "build\I2PChat"
Remove-PathWithRetry -Path "build\I2PChat-tui"
$env:I2PCHAT_OMIT_BUNDLED_I2PD = "1"
try {
    Invoke-NativeChecked $PythonExe @("-m", "PyInstaller", "--clean", "-y", "I2PChat.spec")
    Invoke-NativeChecked $PythonExe @("-m", "PyInstaller", "--clean", "-y", "I2PChat-tui.spec")
}
finally {
    Remove-Item Env:\I2PCHAT_OMIT_BUNDLED_I2PD -ErrorAction SilentlyContinue
}

$WingetZipFile = "dist\\I2PChat-windows-x64-winget-v$ReleaseVersion.zip"
if (Test-Path $WingetZipFile) {
    Remove-Item -Force $WingetZipFile
}
$WingetStage = "dist\\I2PChat-windows-x64-winget-v$ReleaseVersion"
if (Test-Path $WingetStage) {
    Remove-Item -Recurse -Force $WingetStage
}
New-Item -ItemType Directory -Path $WingetStage | Out-Null
Copy-Item -Recurse "dist\\I2PChat" "$WingetStage\\I2PChat"
if (Test-Path $BlindboxInstallSrc) {
    Copy-Item $BlindboxInstallSrc "$WingetStage\\install.sh"
}
Compress-Archive -Path "$WingetStage\\*" -DestinationPath $WingetZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $WingetStage
Write-Host "Packed (winget GUI, no bundled i2pd): $WingetZipFile"

$WingetTuiZipFile = "dist\\I2PChat-windows-tui-x64-winget-v$ReleaseVersion.zip"
if (Test-Path $WingetTuiZipFile) {
    Remove-Item -Force $WingetTuiZipFile
}
$WingetTuiStage = "dist\\I2PChat-windows-tui-x64-winget-v$ReleaseVersion"
if (Test-Path $WingetTuiStage) {
    Remove-Item -Recurse -Force $WingetTuiStage
}
New-Item -ItemType Directory -Path "$WingetTuiStage\\I2PChat" | Out-Null
Copy-Item "dist\\I2PChat-tui\\I2PChat-tui.exe" "$WingetTuiStage\\I2PChat\\"
Copy-Item -Recurse "dist\\I2PChat-tui\\_internal" "$WingetTuiStage\\I2PChat\\_internal"
if (Test-Path "dist\\I2PChat-tui\\vendor") {
    Copy-Item -Recurse "dist\\I2PChat-tui\\vendor" "$WingetTuiStage\\I2PChat\\vendor"
}
if (Test-Path $BlindboxInstallSrc) {
    Copy-Item $BlindboxInstallSrc "$WingetTuiStage\\install.sh"
}
Compress-Archive -Path "$WingetTuiStage\\*" -DestinationPath $WingetTuiZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $WingetTuiStage
Write-Host "Packed (winget TUI, no bundled i2pd): $WingetTuiZipFile"

# Winget pass leaves dist\I2PChat (and dist\I2PChat-tui) as PyInstaller output *without* embedded i2pd.
# Release zip I2PChat-windows-x64-v* was already packed from the earlier full tree. Copy router from
# repo vendor/ back beside the onedirs so local `dist\I2PChat\I2PChat.exe` works with bundled backend.
if (Test-Path "vendor\i2pd\windows-x64\i2pd.exe") {
    foreach ($root in @("dist\I2PChat", "dist\I2PChat-tui")) {
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }
        $BundledRouterDir = Join-Path $root "vendor\i2pd\windows-x64"
        New-Item -ItemType Directory -Force -Path $BundledRouterDir | Out-Null
        Copy-Item "vendor\i2pd\windows-x64\i2pd.exe" (Join-Path $BundledRouterDir "i2pd.exe") -Force
        Get-ChildItem "vendor\i2pd\windows-x64\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName $BundledRouterDir -Force
        }
    }
    Write-Host "==> Restored bundled i2pd beside dist\I2PChat (and TUI onedir if present) for local runs"
}
else {
    Write-Host '==> No vendor\i2pd\windows-x64\i2pd.exe - dist onedirs stay without embedded router (use system i2pd / full release zip)'
}

$sumGui = (Get-FileHash -Path $ZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumTui = (Get-FileHash -Path $TuiZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumWinget = (Get-FileHash -Path $WingetZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumWingetTui = (Get-FileHash -Path $WingetTuiZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$nameGui = Split-Path -Path $ZipFile -Leaf
$nameTui = Split-Path -Path $TuiZipFile -Leaf
$nameWinget = Split-Path -Path $WingetZipFile -Leaf
$nameWingetTui = Split-Path -Path $WingetTuiZipFile -Leaf
Set-Content -Path "SHA256SUMS" -Encoding utf8 -Value @(
    "$sumGui  $nameGui",
    "$sumTui  $nameTui",
    "$sumWinget  $nameWinget",
    "$sumWingetTui  $nameWingetTui"
)
Write-Host "Generated: SHA256SUMS (full + winget zips)"
Write-Host ""
Write-Host "==> winget manifest InstallerSha256 (paste into packaging/winget/*/MetanoicArmor.I2PChat*.installer.yaml)"
Write-Host "  MetanoicArmor.I2PChat:     $sumWinget"
Write-Host "  MetanoicArmor.I2PChat.TUI: $sumWingetTui"

if ($env:I2PCHAT_SKIP_GPG_SIGN -eq "1") {
    Write-Warning 'Skipping GPG detached signature (I2PCHAT_SKIP_GPG_SIGN=1)'
}
elseif (-not ($gpgExe = Get-I2PChatGpgExecutable)) {
    if ($env:I2PCHAT_REQUIRE_GPG -eq "1") {
        throw "gpg is required to create detached release signature (install GnuPG or set I2PCHAT_GPG_EXE to gpg.exe)"
    }
    Write-Warning 'gpg not found; skipping detached signature (install GnuPG, add to PATH, or set I2PCHAT_GPG_EXE; use I2PCHAT_REQUIRE_GPG=1 to enforce)'
}
else {
    $GpgArgs = @("--batch", "--yes", "--armor", "--detach-sign", "--output", "SHA256SUMS.asc")
    if ($env:I2PCHAT_GPG_KEY_ID) {
        $GpgArgs += @("--local-user", $env:I2PCHAT_GPG_KEY_ID)
    }
    $GpgArgs += "SHA256SUMS"
    & $gpgExe @GpgArgs
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
