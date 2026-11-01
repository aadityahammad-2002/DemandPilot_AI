<#
Safe, intentional 2,000 SKU x 100 store x 365 day acceptance entry point.
No PostgreSQL access, schema migration, or modifications to existing source data.
Creates output OUTSIDE the OneDrive-backed project to avoid syncing huge datasets.
Full-scale operation may require substantial CPU/disk resources and may take long.
#>
param(
    [string]$OutputParent = "$env:LOCALAPPDATA\YottaFlex_FullScale_Evidence"
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $project 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $python -PathType Leaf)) {
    throw "Python venv missing: $python. Follow README setup instructions."
}
if (-not (Test-Path (Join-Path $backend 'app\generation\full_gate.py'))) {
    throw 'Full-scale gate source missing.'
}
Write-Host 'YOTTAFLEX REQUIRED FULL-SCALE ACCEPTANCE' -ForegroundColor Cyan
Write-Host 'Profile: 2,000 SKUs x 100 stores x 365 days = 73,000,000 potential SKU-store-days.'
Write-Host 'No application database writes, migrations, or pipeline job submissions.'
Write-Host 'Output parent:' $OutputParent
Write-Host 'Parquet outputs may be very large; ensure sufficient free resources.'
Push-Location $backend
try {
    & $python -c 'import pyarrow, duckdb; from app.generation.config import GeneratorConfig; c=GeneratorConfig(); c.validate(); assert (c.sku_count,c.store_count,c.days,c.store_batch,c.format)==(2000,100,365,2,"parquet"); print("PASS: optional dependencies and full profile")'
    if ($LASTEXITCODE -ne 0) {
        throw 'Missing PyArrow/DuckDB or profile mismatch. Install: backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-phase2.txt (from repo root).'
    }
    if (-not (Test-Path $OutputParent)) { New-Item -ItemType Directory -Path $OutputParent -Force | Out-Null }
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $out = Join-Path $OutputParent ('P1-T02-FULL-' + $stamp)
    if (Test-Path $out) { throw "Existing output target: $out" }
    Write-Host "Output: $out" -ForegroundColor Cyan
    # The Python gate enforces >=18 GiB free disk, >=2 GiB currently available
    # RAM, pilot-based storage estimate, ongoing reserve monitoring and
    # independent logical row checksum verification. No bypasses.
    & $python -m app.generation.full_gate --out $out
    $exitCode = $LASTEXITCODE
    $report = Join-Path $out 'full_scale_acceptance.json'
    Write-Host "Result report: $report"
    if ($exitCode -ne 0) {
        throw "FULL SCALE NOT PASSED. Read $report and stage logs; do NOT claim successful full-scale validation."
    }
    $result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    if ($result.status -ne 'PASS') { throw 'Unexpected full-scale result status; inspect report.' }
    Write-Host 'FULL-SCALE PASS: validated manifest and independent row-level reproducibility.' -ForegroundColor Green
    Write-Host 'Commit only selected summary reports, not full generated Parquet or hidden ground-truth records.'
}
finally { Pop-Location }
