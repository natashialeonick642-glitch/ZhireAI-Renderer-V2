[CmdletBinding()]
param(
    [string]$PreferencesPath = "",
    [switch]$AllDetected,
    [switch]$VerifyOnly,
    [switch]$InstallCodexSkill
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-SafePath {
    param([string]$Root, [string]$Relative)
    $candidate = $Relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    if ([IO.Path]::IsPathRooted($candidate) -or $candidate.Split([IO.Path]::DirectorySeparatorChar) -contains '..') {
        throw "Unsafe manifest path: $Relative"
    }
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $targetFull = [IO.Path]::GetFullPath((Join-Path $Root $candidate))
    if (-not $targetFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest path escapes package root: $Relative"
    }
    return $targetFull
}

function Test-Hashes {
    param([string]$Root, $Hashes, [string]$Label)
    foreach ($item in $Hashes.PSObject.Properties) {
        $target = Get-SafePath -Root $Root -Relative $item.Name
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            throw "$Label file missing: $($item.Name)"
        }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
        if ($actual -ne ([string]$item.Value).ToLowerInvariant()) {
            throw "$Label hash mismatch: $($item.Name)"
        }
    }
}

function Get-C4DProfiles {
    $root = Join-Path $env:APPDATA "Maxon"
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { return @() }
    return @(
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Maxon Cinema 4D (2023|2024|2025|2026)_' } |
            Sort-Object Name -Descending
    )
}

$manifestPath = Join-Path $PSScriptRoot "release-info.json"
$pluginRoot = Join-Path $PSScriptRoot "zhireAI"
$skillRoot = Join-Path $PSScriptRoot "skills\zhire-c4d-prompt-structure"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "release-info.json is missing." }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Test-Hashes -Root $pluginRoot -Hashes $manifest.plugin_sha256 -Label "Plugin"
Test-Hashes -Root $skillRoot -Hashes $manifest.skill_sha256 -Label "Skill"

if ($VerifyOnly) {
    Write-Host "Package verification completed." -ForegroundColor Green
    return
}

$running = @(Get-Process -Name "Cinema 4D" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    throw "Cinema 4D is running. Close it before installation; this installer will not terminate it."
}

if ($PreferencesPath) {
    if (-not (Test-Path -LiteralPath $PreferencesPath -PathType Container)) {
        throw "Cinema 4D preferences directory does not exist."
    }
    $profiles = @((Get-Item -LiteralPath $PreferencesPath))
} else {
    $profiles = @(Get-C4DProfiles)
    if ($profiles.Count -eq 0) { throw "No compatible Cinema 4D user profile was found." }
    if (-not $AllDetected) { $profiles = @($profiles[0]) }
}

$installed = @()
foreach ($profile in $profiles) {
    $pluginsRoot = Join-Path $profile.FullName "plugins"
    $backupRoot = Join-Path $profile.FullName "plugin-backups"
    New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $target = Join-Path $pluginsRoot "zhireAI"
    $temporary = Join-Path $pluginsRoot ("zhireAI.install-" + [Guid]::NewGuid().ToString("N"))
    Copy-Item -LiteralPath $pluginRoot -Destination $temporary -Recurse
    Test-Hashes -Root $temporary -Hashes $manifest.plugin_sha256 -Label "Temporary plugin"
    $backup = ""
    $replacementPlaced = $false
    try {
        if (Test-Path -LiteralPath $target -PathType Container) {
            $backup = Join-Path $backupRoot ("zhireAI-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
            Move-Item -LiteralPath $target -Destination $backup
        }
        Move-Item -LiteralPath $temporary -Destination $target
        $replacementPlaced = $true
        Test-Hashes -Root $target -Hashes $manifest.plugin_sha256 -Label "Installed plugin"
    } catch {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
        if ($replacementPlaced -and (Test-Path -LiteralPath $target)) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        if ($backup -and (Test-Path -LiteralPath $backup)) {
            Move-Item -LiteralPath $backup -Destination $target
        }
        throw
    }
    $installed += [PSCustomObject]@{ Profile = $profile.Name; Plugin = $target; Backup = $backup }
}

if ($InstallCodexSkill) {
    $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
    $skillsRoot = Join-Path $codexRoot "skills"
    $skillTarget = Join-Path $skillsRoot "zhire-c4d-prompt-structure"
    New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null
    if (Test-Path -LiteralPath $skillTarget) {
        $skillBackup = $skillTarget + ".backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Move-Item -LiteralPath $skillTarget -Destination $skillBackup
    }
    Copy-Item -LiteralPath $skillRoot -Destination $skillTarget -Recurse
    Test-Hashes -Root $skillTarget -Hashes $manifest.skill_sha256 -Label "Installed skill"
}

$installed | Format-Table -AutoSize -Wrap
Write-Host "ZhireAI Renderer V2 installation completed and verified." -ForegroundColor Green
Write-Host "Start Cinema 4D and configure your own JuAIHub API Key in the plugin settings."
