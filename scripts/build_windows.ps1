[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DistDirectory = Join-Path $ProjectRoot "dist"
$BundleDirectory = Join-Path $DistDirectory "PlaylistBridge"
$ArchivePath = Join-Path $DistDirectory "PlaylistBridge-windows-x64.zip"

Set-Location $ProjectRoot

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "PlaylistBridge" `
    --paths "src" `
    --specpath "build" `
    --collect-all "ytmusicapi" `
    "scripts/playlistbridge_entry.py"

if (-not (Test-Path $BundleDirectory)) {
    throw "Build non trovata in $BundleDirectory"
}

Copy-Item (Join-Path $ProjectRoot "README.md") $BundleDirectory -Force
Copy-Item (Join-Path $ProjectRoot "docs\GUIDA_WINDOWS.md") `
    (Join-Path $BundleDirectory "GUIDA_WINDOWS.md") -Force

if (Test-Path $ArchivePath) {
    Remove-Item -Force $ArchivePath
}

Compress-Archive -Path (Join-Path $BundleDirectory "*") -DestinationPath $ArchivePath
Write-Host "Build completata: $ArchivePath"
