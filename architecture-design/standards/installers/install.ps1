# Install the UIF Claude naming standards into ~/.claude (Windows).
#
# Idempotent: re-running is the documented update path (git pull; .\install.ps1).
# Plain copies only - no junctions, so no Administrator or Developer Mode needed.
# ASCII only on purpose: Windows PowerShell 5.1 reads unmarked files as ANSI.
#
#   .\installers\install.ps1              copy rules + hook, then print the snippet to paste
#   .\installers\install.ps1 -WireHook    also merge the snippet into ~\.claude\settings.json
#
# Runnable from anywhere: $src resolves to the standards\ directory one level up.
param([switch]$WireHook)
$ErrorActionPreference = 'Stop'

$src        = Split-Path $PSScriptRoot -Parent
$dest       = Join-Path $HOME '.claude'
$rulesDest  = Join-Path $dest 'rules\naming'
$hooksDest  = Join-Path $dest 'hooks'
$version    = (Get-Content (Join-Path $src 'VERSION') -Raw).Trim()
$date       = Get-Date -Format 'yyyy-MM-dd'
try   { $sha = "$(git -C $src rev-parse --short HEAD 2>$null)".Trim() }
catch { $sha = '' }
if (-not $sha) { $sha = 'unknown' }

# 1. rules - replace, not merge, so a rule deleted upstream disappears locally
if (Test-Path $rulesDest) { Remove-Item $rulesDest -Recurse -Force }
New-Item -ItemType Directory -Path $rulesDest -Force | Out-Null
Copy-Item (Join-Path $src 'rules\naming\*') $rulesDest -Recurse -Force

# 2. hook
New-Item -ItemType Directory -Path $hooksDest -Force | Out-Null
Copy-Item (Join-Path $src 'hooks\validate_naming.py') $hooksDest -Force

# 3. version stamp
"version=$version`ncommit=$sha`ninstalled=$date" |
  Set-Content (Join-Path $rulesDest '.installed-version')

Write-Host "UIF naming rules $version ($sha) installed $date"
Write-Host "  rules -> $rulesDest"
Get-ChildItem $rulesDest -Recurse -Filter *.md |
  ForEach-Object { Write-Host ("    " + $_.FullName.Replace($dest, '~\.claude')) }
Write-Host "  hook  -> $(Join-Path $hooksDest 'validate_naming.py')"

$snippetPath  = Join-Path $src 'settings\hooks.snippet.json'
$settingsPath = Join-Path $dest 'settings.json'

# The snippet ships with `python`; use whichever interpreter this machine actually has.
$pyBin = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' }
         elseif (Get-Command python3 -ErrorAction SilentlyContinue) { 'python3' }
         else { throw 'No python interpreter on PATH.' }
$snippetText = (Get-Content $snippetPath -Raw).Replace('"python ', ('"' + $pyBin + ' '))

if ($WireHook) {
    $snippet = $snippetText | ConvertFrom-Json
    $entry   = $snippet.hooks.PreToolUse[0]
    $cmd     = $entry.hooks[0].command
    if (Test-Path $settingsPath) {
        try { $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json }
        catch { throw 'settings.json is not valid JSON - not touching it. Paste the snippet by hand.' }
        Copy-Item $settingsPath "$settingsPath.bak" -Force
    } else {
        $settings = [pscustomobject]@{}
    }
    if (-not $settings.PSObject.Properties['hooks']) {
        $settings | Add-Member hooks ([pscustomobject]@{})
    }
    if (-not $settings.hooks.PSObject.Properties['PreToolUse']) {
        $settings.hooks | Add-Member PreToolUse @()
    }
    $existing = @($settings.hooks.PreToolUse | ForEach-Object { $_.hooks } |
                  ForEach-Object { $_.command })
    if ($existing -contains $cmd) {
        Write-Host '  hook already wired in settings.json - nothing to do'
    } else {
        $settings.hooks.PreToolUse = @($settings.hooks.PreToolUse) + $entry
        ($settings | ConvertTo-Json -Depth 12) | Set-Content $settingsPath
        Write-Host "  hook wired into $settingsPath (previous version saved as settings.json.bak)"
    }
} else {
    Write-Host ''
    Write-Host 'The rules are active now. To enable the naming guard hook, paste this into'
    Write-Host "$settingsPath (merge into an existing hooks block; do not overwrite it):"
    Write-Host ''
    Write-Host $snippetText
    Write-Host 'Or re-run with -WireHook to merge it programmatically.'
}
