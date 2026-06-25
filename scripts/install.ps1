<#
.SYNOPSIS
    Guided installer for drone-check (Windows).

.DESCRIPTION
    Sets up drone-check in a local virtual environment and, optionally, the
    WSL-based "View in Configurator" (SITL) feature. Designed to be run by
    non-technical users with copy & paste.

    Run it from the repository root, e.g.:

        powershell -ExecutionPolicy Bypass -File scripts\install.ps1

    Without options it asks whether to set up the Configurator/SITL feature.
    Use the switches below to run unattended.

.PARAMETER Dev
    Also install development dependencies (pytest).

.PARAMETER Sitl
    Set up the WSL + SITL "View in Configurator" feature without asking.

.PARAMETER NoSitl
    Skip the SITL feature without asking (capture & rules still work fully).

.PARAMETER SitlBundle
    Path to a pre-built SITL bundle (from `drone-check sitl package`) to install,
    so no toolchain or compiling is needed on this machine.

.PARAMETER BfcdBundle
    Path to a pre-built bf-configd bundle (from `drone-check bfcd package`) to
    install. If neither bundle is given they are downloaded from the release.

.PARAMETER Distro
    WSL distro to use for the Configurator binaries (default: Ubuntu).
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Sitl,
    [switch]$NoSitl,
    [string]$SitlBundle = "",
    [string]$BfcdBundle = "",
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$GhRepo = "PSi86/drone-check"   # repo hosting the binary bundle assets
$GhAssetTag = "binaries"         # dedicated release tag the bundles are attached to

function Info($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "    $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "    $m" -ForegroundColor Yellow }
function Err($m)   { Write-Host "!!  $m" -ForegroundColor Red }

# Work from the repository root (the parent of this script's folder).
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Info "drone-check installer - repository: $RepoRoot"

# --- 1. Python -------------------------------------------------------------
function Get-PyVersion($cand) {
    try {
        $out = (& $cand --version 2>&1) | Out-String
        if ($out -match '(\d+)\.(\d+)\.(\d+)') {
            return [version]("{0}.{1}.{2}" -f $Matches[1], $Matches[2], $Matches[3])
        }
    } catch {}
    return $null
}

Info "Checking Python (3.10+ required)..."
$py = $null
$pyVer = $null
foreach ($cand in @("python", "py")) {
    if (-not (Get-Command $cand -ErrorAction SilentlyContinue)) { continue }
    $ver = Get-PyVersion $cand
    if ($ver -and $ver -ge [version]"3.10.0") { $py = $cand; $pyVer = $ver; break }
}
if (-not $py) {
    Err "Python 3.10+ not found."
    Warn "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'),"
    Warn "or run:  winget install Python.Python.3.12"
    Warn "Then re-run this installer."
    exit 1
}
Ok "Found Python $pyVer ($py)"

# --- 2. Virtual environment + package -------------------------------------
$venvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Info "Creating virtual environment (.venv)..."
    & $py -m venv .venv
}
Ok "Virtual environment ready (.venv)"

Info "Installing drone-check and its dependencies (this may take a minute)..."
& $venvPy -m pip install --upgrade pip --quiet
$spec = if ($Dev) { ".[dev]" } else { "." }
& $venvPy -m pip install -e $spec
Ok "drone-check installed. Run it as:  .\.venv\Scripts\drone-check.exe SUBCOMMAND"

# --- 3. Optional: WSL + SITL ("View in Configurator") ----------------------
$wantSitl = $false
if ($Sitl)        { $wantSitl = $true }
elseif ($NoSitl)  { $wantSitl = $false }
else {
    Write-Host ""
    Write-Host "The 'View in Configurator' feature opens a stored capture in the real" -ForegroundColor White
    Write-Host "Betaflight Configurator (via bf-configd, the read-only default, or SITL)" -ForegroundColor White
    Write-Host "under WSL. Capture and rule-checking work fully WITHOUT it." -ForegroundColor White
    $ans = Read-Host "Set up the Configurator feature now? (needs WSL) [Y/n]"
    $wantSitl = ($ans -notmatch '^(n|no|nein)$')
}

if (-not $wantSitl) {
    Info "Skipping the SITL feature. It will be hidden in the UI until WSL is set up."
    Write-Host ""
    Ok "Done. Start drone-check with:"
    Ok "    .\.venv\Scripts\drone-check.exe serve"
    Ok "then open http://127.0.0.1:8000"
    exit 0
}

# 3a. WSL present?
Info "Checking WSL..."
$wslOk = $false
$wslExe = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wslExe) {
    try {
        # wsl.exe prints UTF-16LE; PowerShell 5.1 captures it with stray NUL bytes
        # between characters, so strip them before matching the distro name.
        $distros = ((& wsl.exe -l -q) -join "`n") -replace "`0", ""
        if ($distros -match [regex]::Escape($Distro)) { $wslOk = $true }
    } catch {}
}
if (-not $wslExe) {
    Err "WSL is not installed."
    Warn "Install it (Administrator PowerShell), then REBOOT and re-run this installer:"
    Warn "    wsl --install -d $Distro"
    exit 1
}
if (-not $wslOk) {
    Err "WSL is present but the '$Distro' distro is not installed."
    Warn "Install it, then re-run this installer:"
    Warn "    wsl --install -d $Distro"
    Warn "(On first launch the distro asks you to create a Linux username/password.)"
    exit 1
}
Ok "WSL with '$Distro' is available."

# 3b. Configurator binaries: install pre-built bundles (no toolchain needed).
# Download the *-bundle release asset whose name contains $pat to $out, from the
# dedicated public 'binaries' release. Uses the GitHub API (no auth needed);
# falls back to the GitHub CLI.
function Get-Asset($pat, $out) {
    Info "Fetching $pat from the '$GhAssetTag' release ..."
    try {
        $rel = Invoke-RestMethod -UseBasicParsing `
            -Uri "https://api.github.com/repos/$GhRepo/releases/tags/$GhAssetTag" `
            -Headers @{ 'User-Agent' = 'drone-check-installer' }
        $asset = $rel.assets | Where-Object { $_.name -like "$pat*.tar.gz" } | Select-Object -First 1
        if ($asset) {
            Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $out
            if (Test-Path $out) { return $true }
        }
    } catch {}
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try {
            gh release download $GhAssetTag --repo $GhRepo --pattern "$pat*.tar.gz" --output $out --clobber | Out-Null
        } catch {}
        $ErrorActionPreference = $prev
        if (Test-Path $out) { return $true }
    }
    return $false
}

# Provision one backend: prefer an explicit/local bundle, else download it.
function Install-Backend($name, $bundle, $buildHint) {
    if ($name -eq "bfcd") { $label = "bf-configd" } else { $label = "SITL" }
    if (-not $bundle) {
        $found = Get-ChildItem -Path $RepoRoot -Filter "$name-bundle*.tar.gz" -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($found) { $bundle = $found.FullName }
    }
    if (-not $bundle) {
        $tmp = Join-Path $env:TEMP "$name-bundle.tar.gz"
        if (Get-Asset "$name-bundle" $tmp) { $bundle = $tmp }
    }
    if ($bundle -and (Test-Path $bundle)) {
        Info "Installing pre-built $label binaries ..."
        & $venvPy -m drone_check $name install "$bundle"
        if ($LASTEXITCODE -eq 0) { Ok "$label binaries installed." }
        else { Warn "$label bundle install failed (see above)." }
    } else {
        Warn "No $label bundle found locally or on the '$GhAssetTag' release."
        Warn "  install one you were given:  .\.venv\Scripts\drone-check.exe $name install <bundle.tar.gz>"
        Warn "  or build from source:        $buildHint"
    }
}

# bf-configd is the default backend, so provision it first; SITL is the alternative.
# The build hint covers every firmware version we ship a backend for, so a
# from-source install matches the bundle's coverage.
$AllVersions = "4.4.0 4.5.0 4.5.1 4.5.2 4.5.3 4.5.4 2025.12.1 2025.12.2 2025.12.3 2025.12.4"
Install-Backend "bfcd" $BfcdBundle "wsl -d $Distro -- bash scripts/build_bfcd.sh $AllVersions"
Install-Backend "sitl" $SitlBundle "wsl -d $Distro -- bash scripts/build_sitl.sh $AllVersions"

Write-Host ""
Info "Cached bf-configd versions:"; & $venvPy -m drone_check bfcd list
Info "Cached SITL versions:";       & $venvPy -m drone_check sitl list
Write-Host ""
Ok "Done. Start drone-check with:"
Ok "    .\.venv\Scripts\drone-check.exe serve"
Ok "then open http://127.0.0.1:8000"
