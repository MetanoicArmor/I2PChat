Param(
    [string]$VenvDir = ".venv"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$BlindboxPackaging = "cpp\apps\blindbox-daemon\packaging"

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
    foreach ($procName in @("I2PChat", "I2PChat-tui", "i2pchat-gui", "i2pchat-tui", "i2pchat-blindbox-daemon")) {
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

function Copy-BundledI2pdFromSource {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir
    )
    if (-not (Test-Path -LiteralPath $SourceDir)) {
        return $false
    }
    $src = Join-Path $SourceDir "windows-x64\i2pd.exe"
    $dst = "vendor\i2pd\windows-x64\i2pd.exe"
    if (Test-Path -LiteralPath $src) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item $src $dst -Force
        Get-ChildItem (Join-Path $SourceDir "windows-x64\*.dll") -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName (Split-Path -Parent $dst) -Force
        }
        return $true
    }
    return $false
}

function Copy-I2pdInto {
    param([Parameter(Mandatory = $true)][string]$Root)
    if ($env:I2PCHAT_OMIT_BUNDLED_I2PD -eq "1") {
        return
    }
    if (-not (Test-Path "vendor\i2pd\windows-x64\i2pd.exe")) {
        return
    }
    $dir = Join-Path $Root "vendor\i2pd\windows-x64"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Copy-Item "vendor\i2pd\windows-x64\i2pd.exe" (Join-Path $dir "i2pd.exe") -Force
    Get-ChildItem "vendor\i2pd\windows-x64\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName $dir -Force
    }
}

function Publish-CppTree {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Dest,
        [switch]$OmitI2pd
    )
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    Copy-Item (Join-Path $Stage "bin\i2pchat-gui.exe") (Join-Path $Dest "I2PChat.exe") -Force
    Copy-Item (Join-Path $Stage "bin\i2pchat-tui.exe") (Join-Path $Dest "I2PChat-tui.exe") -Force
    $daemon = Join-Path $Stage "bin\i2pchat-blindbox-daemon.exe"
    if (Test-Path $daemon) {
        Copy-Item $daemon (Join-Path $Dest "i2pchat-blindbox-daemon.exe") -Force
    }
    Get-ChildItem (Join-Path $Stage "bin\*.dll") -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName $Dest -Force
    }
    if (-not $OmitI2pd) {
        Copy-I2pdInto -Root $Dest
    }
}

function Invoke-WinDeployQt {
    param([Parameter(Mandatory = $true)][string]$Exe)
    $tool = $null
    $cmd = Get-Command windeployqt -ErrorAction SilentlyContinue
    if ($cmd) {
        $tool = $cmd.Source
    }
    elseif ($env:QTDIR) {
        $candidate = Join-Path $env:QTDIR "bin\windeployqt.exe"
        if (Test-Path $candidate) {
            $tool = $candidate
        }
    }
    if ($tool) {
        Write-Host "==> windeployqt $Exe"
        & $tool $Exe --release --no-translations
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "windeployqt failed with exit $LASTEXITCODE; Qt plugins may be missing"
        }
    }
    else {
        Write-Warning "windeployqt not on PATH; GUI zip needs a machine with Qt 6 DLLs or set QTDIR"
    }
}

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$VersionFile = Join-Path $RepoRoot "VERSION"
if (-not (Test-Path -LiteralPath $VersionFile)) {
    throw "VERSION file not found: $VersionFile"
}
$ReleaseVersion = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
if (-not $ReleaseVersion) {
    throw "VERSION file is empty: $VersionFile"
}

if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "cmake is required (https://cmake.org/download/ or Visual Studio installer)."
}

if ($env:I2PCHAT_OMIT_BUNDLED_I2PD -ne "1" -and -not (Test-Path "vendor\i2pd\windows-x64\i2pd.exe")) {
    Write-Host "==> Checking optional bundled Windows i2pd source"
    if ($env:I2PCHAT_BUNDLED_I2PD_SOURCE_DIR) {
        [void](Copy-BundledI2pdFromSource -SourceDir $env:I2PCHAT_BUNDLED_I2PD_SOURCE_DIR)
    }
    elseif (Test-Path "..\i2pchat-bundled-i2pd") {
        [void](Copy-BundledI2pdFromSource -SourceDir "..\i2pchat-bundled-i2pd")
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
                }
                else {
                    Push-Location $cacheDir
                    try { & git pull --ff-only 2>$null | Out-Null } finally { Pop-Location }
                }
                [void](Copy-BundledI2pdFromSource -SourceDir $cacheDir)
            }
        }
    }
    if (-not (Test-Path "vendor\i2pd\windows-x64\i2pd.exe")) {
        Write-Host "==> Bundled i2pd: NOT FOUND; building without embedded router"
    }
}

$BuildDir = Join-Path $RepoRoot "cpp\build-release"
$Stage = Join-Path $RepoRoot "dist\cpp-install"
Stop-I2PChatProcessesLockingDist
Remove-PathWithRetry -Path $Stage
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

$cmakeArgs = @(
    "-S", (Join-Path $RepoRoot "cpp"),
    "-B", $BuildDir,
    "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_INSTALL_PREFIX=$Stage",
    "-DI2PCHAT_BUILD_TESTS=OFF",
    "-DI2PCHAT_BUILD_TUI=ON",
    "-DI2PCHAT_BUILD_GUI=ON"
)
if ($env:CMAKE_TOOLCHAIN_FILE) {
    $cmakeArgs += "-DCMAKE_TOOLCHAIN_FILE=$($env:CMAKE_TOOLCHAIN_FILE)"
}
elseif ($env:VCPKG_ROOT) {
    $tc = Join-Path $env:VCPKG_ROOT "scripts\buildsystems\vcpkg.cmake"
    if (Test-Path $tc) {
        $cmakeArgs += "-DCMAKE_TOOLCHAIN_FILE=$tc"
        if (-not $env:VCPKG_MANIFEST_FEATURES) {
            $env:VCPKG_MANIFEST_FEATURES = "tui;gui"
        }
    }
}
if ($env:CMAKE_PREFIX_PATH) {
    $cmakeArgs += "-DCMAKE_PREFIX_PATH=$($env:CMAKE_PREFIX_PATH)"
}

Write-Host "==> CMake configure (C++ GUI + TUI)"
Invoke-NativeChecked "cmake" $cmakeArgs
Write-Host "==> CMake build"
Invoke-NativeChecked "cmake" @("--build", $BuildDir, "--config", "Release", "--parallel")
Write-Host "==> CMake install"
Invoke-NativeChecked "cmake" @("--install", $BuildDir, "--config", "Release", "--prefix", $Stage)

$guiExe = Join-Path $Stage "bin\i2pchat-gui.exe"
$tuiExe = Join-Path $Stage "bin\i2pchat-tui.exe"
if (-not (Test-Path $guiExe) -or -not (Test-Path $tuiExe)) {
    throw "CMake install did not produce i2pchat-gui.exe / i2pchat-tui.exe in $Stage\bin"
}

Write-Host "==> Stage dist\I2PChat"
Remove-PathWithRetry -Path "dist\I2PChat"
Publish-CppTree -Stage $Stage -Dest "dist\I2PChat"
Invoke-WinDeployQt -Exe "dist\I2PChat\I2PChat.exe"

Write-Host ""
Write-Host "Done."
Write-Host "GUI binary: dist\I2PChat\I2PChat.exe"
Write-Host "TUI binary: dist\I2PChat\I2PChat-tui.exe"
Write-Host "Release $ReleaseVersion"

$ZipFile = "dist\I2PChat-windows-x64-v$ReleaseVersion.zip"
if (Test-Path $ZipFile) { Remove-Item -Force $ZipFile }
$ZipStage = "dist\I2PChat-windows-x64-v$ReleaseVersion"
if (Test-Path $ZipStage) { Remove-Item -Recurse -Force $ZipStage }
New-Item -ItemType Directory -Path $ZipStage | Out-Null
Copy-Item -Recurse "dist\I2PChat" "$ZipStage\I2PChat"
if (Test-Path $BlindboxPackaging) {
    New-Item -ItemType Directory -Path "$ZipStage\blindbox-daemon" | Out-Null
    Copy-Item "$BlindboxPackaging\*" "$ZipStage\blindbox-daemon\" -ErrorAction SilentlyContinue
}
Compress-Archive -Path "$ZipStage\*" -DestinationPath $ZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $ZipStage
Write-Host "Packed: $ZipFile"

$TuiZipFile = "dist\I2PChat-windows-tui-x64-v$ReleaseVersion.zip"
if (Test-Path $TuiZipFile) { Remove-Item -Force $TuiZipFile }
$TuiStage = "dist\I2PChat-windows-tui-x64-v$ReleaseVersion"
if (Test-Path $TuiStage) { Remove-Item -Recurse -Force $TuiStage }
New-Item -ItemType Directory -Path "$TuiStage\I2PChat" | Out-Null
Copy-Item "dist\I2PChat\I2PChat-tui.exe" "$TuiStage\I2PChat\"
Get-ChildItem "dist\I2PChat\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName "$TuiStage\I2PChat\"
}
if (Test-Path "dist\I2PChat\vendor") {
    Copy-Item -Recurse "dist\I2PChat\vendor" "$TuiStage\I2PChat\vendor"
}
Compress-Archive -Path "$TuiStage\*" -DestinationPath $TuiZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $TuiStage
Write-Host "Packed (TUI only): $TuiZipFile"

Write-Host ""
Write-Host "==> Winget zips (no embedded i2pd)"
$WingetZipFile = "dist\I2PChat-windows-x64-winget-v$ReleaseVersion.zip"
if (Test-Path $WingetZipFile) { Remove-Item -Force $WingetZipFile }
$WingetStage = "dist\I2PChat-windows-x64-winget-v$ReleaseVersion"
if (Test-Path $WingetStage) { Remove-Item -Recurse -Force $WingetStage }
New-Item -ItemType Directory -Path $WingetStage | Out-Null
Copy-Item -Recurse "dist\I2PChat" "$WingetStage\I2PChat"
$wingetI2pd = "$WingetStage\I2PChat\vendor\i2pd"
if (Test-Path $wingetI2pd) { Remove-Item -Recurse -Force $wingetI2pd }
Compress-Archive -Path "$WingetStage\*" -DestinationPath $WingetZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $WingetStage
Write-Host "Packed (winget GUI, no bundled i2pd): $WingetZipFile"

$WingetTuiZipFile = "dist\I2PChat-windows-tui-x64-winget-v$ReleaseVersion.zip"
if (Test-Path $WingetTuiZipFile) { Remove-Item -Force $WingetTuiZipFile }
$WingetTuiStage = "dist\I2PChat-windows-tui-x64-winget-v$ReleaseVersion"
if (Test-Path $WingetTuiStage) { Remove-Item -Recurse -Force $WingetTuiStage }
New-Item -ItemType Directory -Path "$WingetTuiStage\I2PChat" | Out-Null
Copy-Item "dist\I2PChat\I2PChat-tui.exe" "$WingetTuiStage\I2PChat\"
Get-ChildItem "dist\I2PChat\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName "$WingetTuiStage\I2PChat\"
}
Compress-Archive -Path "$WingetTuiStage\*" -DestinationPath $WingetTuiZipFile -CompressionLevel Optimal
Remove-Item -Recurse -Force $WingetTuiStage
Write-Host "Packed (winget TUI, no bundled i2pd): $WingetTuiZipFile"

$sumGui = (Get-FileHash -Path $ZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumTui = (Get-FileHash -Path $TuiZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumWinget = (Get-FileHash -Path $WingetZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
$sumWingetTui = (Get-FileHash -Path $WingetTuiZipFile -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -Path "SHA256SUMS" -Encoding utf8 -Value @(
    "$sumGui  $(Split-Path -Path $ZipFile -Leaf)",
    "$sumTui  $(Split-Path -Path $TuiZipFile -Leaf)",
    "$sumWinget  $(Split-Path -Path $WingetZipFile -Leaf)",
    "$sumWingetTui  $(Split-Path -Path $WingetTuiZipFile -Leaf)"
)
Write-Host "Generated: SHA256SUMS"
Write-Host "  MetanoicArmor.I2PChat:     $sumWinget"
Write-Host "  MetanoicArmor.I2PChat.TUI: $sumWingetTui"

if ($env:I2PCHAT_SKIP_GPG_SIGN -eq "1") {
    Write-Warning "Skipping GPG detached signature (I2PCHAT_SKIP_GPG_SIGN=1)"
}
elseif (-not ($gpgExe = Get-I2PChatGpgExecutable)) {
    if ($env:I2PCHAT_REQUIRE_GPG -eq "1") {
        throw "gpg is required (install GnuPG or set I2PCHAT_GPG_EXE)"
    }
    Write-Warning "gpg not found; skipping detached signature"
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
            throw "gpg failed with exit code $LASTEXITCODE"
        }
        Write-Warning "gpg signing failed; continuing without detached signature"
    }
    else {
        Write-Host "Generated: SHA256SUMS.asc"
    }
}
