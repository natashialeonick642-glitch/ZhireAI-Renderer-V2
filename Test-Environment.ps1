[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$isSupportedWindows = $env:OS -eq "Windows_NT" -and [Environment]::Is64BitOperatingSystem
$maxonRoot = Join-Path $env:APPDATA "Maxon"
$profiles = @()
if (Test-Path -LiteralPath $maxonRoot -PathType Container) {
    $profiles = @(
        Get-ChildItem -LiteralPath $maxonRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Maxon Cinema 4D (2023|2024|2025|2026)_' } |
            Sort-Object Name -Descending
    )
}

$result = [PSCustomObject]@{
    Windows64Bit = $isSupportedWindows
    PowerShell = $PSVersionTable.PSVersion.ToString()
    Cinema4DProfiles = $profiles.Count
    VerifiedTargetPresent = [bool]($profiles | Where-Object { $_.Name -match '^Maxon Cinema 4D 2025_' })
    ExternalPythonPackages = "none"
    ApiEndpoint = "https://api.juaihub.cn"
}

$result | Format-List
if (-not $isSupportedWindows) {
    throw "This release requires 64-bit Windows 10 or Windows 11."
}
if ($profiles.Count -eq 0) {
    throw "No compatible Cinema 4D user profile was found. Start Cinema 4D once, close it, then retry."
}

Write-Host "Environment check completed." -ForegroundColor Green
