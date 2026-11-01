<#
P1-T02 full scale acceptance. Execute from existing backend with active venv.
ADD-ONLY helper: no database writes, migrations, code replacement, or prior evidence overwrites.
Does not rerun already-passed Phase 2 smoke/pytest.
Full profile includes independent all-partition logical regeneration without 2x disk.
#>
$ErrorActionPreference = 'Stop'
$python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$parent = (Resolve-Path '..\evidence').Path
$root = Join-Path $parent ('P1-T02-FULL-'+$stamp)
# Never select an existing directory, even with the same timestamp.
if (Test-Path $root) { throw "Evidence target already exists: $root" }
if (-not (Test-Path '.\app\generation\full_gate.py') -or
    -not (Test-Path '.\app\generation\full_reproduce.py')) {
    throw 'Missing full-scale gate/reproduce Python modules'
}
Write-Host '=== P1-T02 FULL-SCALE VERIFICATION ===' -ForegroundColor Cyan
Write-Host "Evidence path: $root"
Write-Host 'PostgreSQL will NOT be accessed; completed smoke tests will NOT be repeated.'
& $python -m app.generation.full_gate --out $root
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Write-Host "STOP | Full-scale gate FAILED, exit $rc" -ForegroundColor Red
    Write-Host "Inspect $root\full_scale_acceptance.json and the stage logs."
    exit $rc
}
Write-Host 'PASS | Full-scale acceptance gate finished' -ForegroundColor Green
Write-Host "Report: $root\full_scale_acceptance.json"
Write-Host "Checksum evidence: $root\full_reproducibility.json"
