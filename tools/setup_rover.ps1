# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Avnet
# Authors: Zackary Andraka <zackary.andraka@avnet.com> et al.
#
# Sets up the rover app on a VENTUNO Q over SSH, from Windows. See the README.
#
#   .\setup_rover.ps1 cert -Duid MYUNIQUEID [-Board HOST] [-User USER]
#   .\setup_rover.ps1 install -Config iotcDeviceConfig.json [-Board HOST] [-User USER]

param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet("cert", "install")][string]$Step,
    [string]$Board = "arduino.local",
    [string]$User = "arduino",
    [string]$Duid,
    [string]$Config
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $PSScriptRoot
$BoardScript = Join-Path $PSScriptRoot "board_setup.sh"
$Target = "$User@$Board"
$SshOpts = @("-o", "StrictHostKeyChecking=accept-new")

# Windows PowerShell can't redirect a file into a native command's input, so the board script
# goes through cmd. Its CRLF line endings are stripped on the board.
function Invoke-BoardScript([string]$Arguments) {
    cmd /c "ssh $($SshOpts -join ' ') $Target `"tr -d '\r' | bash -s -- $Arguments`" < `"$BoardScript`""
    if ($LASTEXITCODE -ne 0) {
        throw "The board reported an error (exit code $LASTEXITCODE)."
    }
}

if ($Step -eq "cert") {
    if (-not $Duid) { throw "-Duid is required." }
    if ($Duid -notmatch "^[A-Za-z0-9-]+$") { throw "The Unique ID can only contain letters, numbers and hyphens." }
    Write-Host "Connecting to $Target. Enter the board's password if asked."
    Invoke-BoardScript "cert '$Duid'"
    exit
}

if (-not $Config) { throw "-Config is required." }
$ConfigPath = (Resolve-Path $Config).Path
$Stage = Join-Path ([IO.Path]::GetTempPath()) ("rover-setup-" + [guid]::NewGuid())
$StageApp = Join-Path $Stage "rover"
New-Item -ItemType Directory $StageApp | Out-Null
try {
    Copy-Item (Join-Path $RepoDir "app\app.yaml") $StageApp
    Copy-Item (Join-Path $RepoDir "app\sketch") $StageApp -Recurse
    Copy-Item (Join-Path $RepoDir "app\python") $StageApp -Recurse
    Remove-Item (Join-Path $StageApp "python\__pycache__") -Recurse -ErrorAction SilentlyContinue
    Copy-Item $ConfigPath (Join-Path $StageApp "iotcDeviceConfig.json")
    $Bundle = Join-Path $Stage "rover-setup.tgz"
    tar -czf $Bundle -C $StageApp .
    if ($LASTEXITCODE -ne 0) { throw "tar failed." }

    Write-Host "Copying the rover app to $Target. Enter the board's password if asked."
    scp @SshOpts -q $Bundle "${Target}:/tmp/rover-setup.tgz"
    if ($LASTEXITCODE -ne 0) { throw "Copying to the board failed." }
    Write-Host "Installing. Enter the board's password again if asked."
    Invoke-BoardScript "install"
}
finally {
    Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
}
