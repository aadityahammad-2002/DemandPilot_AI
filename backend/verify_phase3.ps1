<#
P1-T03 / PHASE 3 — Consolidated Windows local forecasting verification.
No generator full-scale runs, no PostgreSQL connections/migrations, no source overwrites.
Run from existing backend directory, .venv and Phase 2 data present.
#>
param(
    [string]$DataRoot = "..\examples\smoke_run",
    [ValidateRange(1,200000)][int]$MaxPairs = 48,
    [switch]$SkipDependencyInstall
)
$ErrorActionPreference = "Stop"
$stage = "PRECHECK"
$status = "FAIL"
$records = [System.Collections.Generic.List[object]]::new()

$python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$data = (Resolve-Path $DataRoot).Path
if (-not (Test-Path (Join-Path $data 'manifest.json'))) {
    throw "Missing Phase 2 dataset manifest: $data"
}
if (-not (Test-Path '.\requirements-phase3.txt')) {
    throw "Missing Phase 3 dependency specification"
}
$evidenceBase = (Resolve-Path '..\evidence').Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$root = Join-Path $evidenceBase "P1-T03-$stamp"
New-Item -ItemType Directory -Path $root -ErrorAction Stop | Out-Null

function Invoke-Gate([string]$Name, [string[]]$Argv) {
    $log = Join-Path $root "$Name.log"
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $python @Argv *> $log
    $code = $LASTEXITCODE
    Get-Content -LiteralPath $log
    $records.Add([pscustomobject]@{name=$Name;exit_code=$code;log=$log})
    if ($code -ne 0) { throw "$Name FAILED, exit=$code, evidence=$log" }
}

try {
    if (-not $SkipDependencyInstall) {
        $stage = 'DEPENDENCIES'
        Invoke-Gate 'dependencies' @('-m','pip','install','-r','requirements-phase3.txt')
    }
    $stage = 'PIP_CHECK'
    Invoke-Gate 'pip_check' @('-m','pip','check')
    $stage = 'BACKEND_PYTEST'
    Invoke-Gate 'backend_pytest' @('-m','pytest','-q')
    $stage = 'FORECAST_TRAIN_EVALUATE'
    $predictionDir = Join-Path $root 'forecast_run'
    Invoke-Gate 'forecast_train_evaluate' @('-m','app.forecasting.cli', 'train-evaluate', '--data', $data, '--out', $predictionDir, '--max-pairs',"$MaxPairs")
    $stage = 'FORECAST_MANIFEST_VALIDATION'
    Invoke-Gate 'forecast_verify' @('-m','app.forecasting.cli','verify','--out',$predictionDir)
    $stage = 'POST_FREEZE_ORACLE'
    Invoke-Gate 'oracle_evaluation' @('-m','app.forecasting.cli','oracle-evaluate','--data',$data,'--out',$predictionDir)
    $stage = 'SAME_SEED_REPRODUCIBILITY'
    $reproDir = Join-Path $root 'forecast_reproduce'
    Invoke-Gate 'reproducibility' @('-m','app.forecasting.cli','reproduce','--data',$data,'--out',$reproDir,'--max-pairs',"$MaxPairs")
    $comparison = Get-Content (Join-Path $reproDir 'reproducibility.json') -Raw | ConvertFrom-Json
    if ($comparison.status -ne 'PASS' -or $comparison.differences.Count -gt 0) {
        throw "Independent forecasting reproducibility mismatch"
    }
    $forecastManifest = Get-Content (Join-Path $predictionDir 'forecast_manifest.json') -Raw | ConvertFrom-Json
    if ($forecastManifest.database_mutations -ne $false -or $forecastManifest.rows_exported -le 0) {
        throw "Forecast manifest invariants failed"
    }
    $status = 'PASS'
}
catch {
    Write-Host "`nSTOP: $stage FAILED: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    $report = [ordered]@{
        task='P1-T03';phase='Demand Forecasting Engine'
        environment='candidate-Windows-local'
        status=$status;failed_stage=$(if ($status -eq 'PASS') { $null } else { $stage })
        date=(Get-Date).ToString('o')
        input_dataset=$data;full_scale_execution='DEFERRED_NOT_EXECUTED'
        database_changes=$false
        stages=@($records.ToArray())
        personal_review='PENDING'
    }
    $summaryFile = Join-Path $root 'P1-T03-local-verification.json'
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryFile
    Write-Host "`n=== P1-T03 FINAL STATUS: $status ==="
    Write-Host "Evidence: $root"
}
if ($status -ne 'PASS') { exit 1 }
