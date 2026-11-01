# Merge the reviewed final archive into the existing clean working directory.
# Preserves .git, backend/.env, .venv, node_modules and run-time evidence.
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target
)
$ErrorActionPreference = 'Stop'
$src = (Resolve-Path -LiteralPath $Source).Path
$dst = (Resolve-Path -LiteralPath $Target).Path
if ($src -eq $dst) { throw 'Source and target cannot be the same directory' }
if (-not (Test-Path (Join-Path $src 'SUBMISSION_FILE_MANIFEST.json'))) { throw 'Missing signed manifest source' }
if (-not (Test-Path (Join-Path $dst 'backend\app\models.py'))) { throw 'Target is not a YottaFlex working project' }
$manifest = Get-Content -LiteralPath (Join-Path $src 'SUBMISSION_FILE_MANIFEST.json') -Raw | ConvertFrom-Json
$paths = @($manifest.files | ForEach-Object { $_.path })
$paths += 'SUBMISSION_FILE_MANIFEST.json'
foreach ($rel in $paths) {
    if ($rel -match '^(/|\\)' -or $rel -match '(^|[\\/])\.\.([\\/]|$)') { throw "Unsafe path: $rel" }
    if ($rel -match '(^|/)(\.git|node_modules|\.venv)/' -or $rel -match '(^|/)\.env($|\.)' -and $rel -notmatch '\.example$') { throw "Runtime path forbidden: $rel" }
}
$backup = Join-Path $env:LOCALAPPDATA ('YottaFlex_Final_Backups\Merge_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
# Validate the entire source BEFORE copying any file.
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    $file = Join-Path $src ($relative.Replace('/','\'))
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing release file: $relative" }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Release hash mismatch: $relative" }
}
$changed = 0
foreach ($rel in $paths) {
    $file = Join-Path $src ($rel.Replace('/','\'))
    $targetFile = Join-Path $dst ($rel.Replace('/','\'))
    if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
        $same = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
        if ($same) { continue }
        $original = Join-Path $backup ($rel.Replace('/','\'))
        New-Item -ItemType Directory -Path (Split-Path $original) -Force | Out-Null
        Copy-Item -LiteralPath $targetFile -Destination $original -ErrorAction Stop
    }
    New-Item -ItemType Directory -Path (Split-Path $targetFile) -Force | Out-Null
    Copy-Item -LiteralPath $file -Destination $targetFile -ErrorAction Stop
    $changed++
}
Write-Host "MERGE PASS | files updated/added: $changed" -ForegroundColor Green
Write-Host "Previous versions backed up under $backup"
Write-Host 'Runtime configuration and Git repository were not deliberately touched.'
Write-Host 'Run tests, inspect git diff, then stage curated source explicitly.'
