<#
P1-T04 / PHASE 4 — Consolidated Windows small-scale verification.
Add-only module; existing models, migrations and earlier evidence unchanged.
Does NOT generate full-scale data, connect to PostgreSQL or mutate its records.
From backend directory with .venv activated.
#>
param(
    [string]$DataRoot = "..\examples\smoke_run",
    [string]$ForecastRoot = "",
    [ValidateRange(1,200000)][int]$MaxPairs = 36,
    [switch]$SkipDependencyInstall
)
$ErrorActionPreference = "Stop"
$stage = "PRECHECK"
$status = "FAIL"
$failure = $null
$gates = [System.Collections.Generic.List[object]]::new()

$python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
$data = (Resolve-Path $DataRoot).Path
if (-not (Test-Path (Join-Path $data 'manifest.json'))) {
    throw "Missing Phase 2 manifest at: $data"
}
if (-not (Test-Path '.\requirements-phase4.txt')) {
    throw 'Missing Phase 4 dependency specification'
}
$evidenceBase = (Resolve-Path '..\evidence').Path
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$root = Join-Path $evidenceBase "P1-T04-$timestamp"
New-Item -ItemType Directory -Path $root -ErrorAction Stop | Out-Null

function Invoke-Gate([string]$Name,[string[]]$Argv) {
    $log = Join-Path $root "$Name.log"
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    # Native-command errors must not evade a stop-on-failure gate.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $python @Argv *> $log
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $oldPreference }
    if (Test-Path $log) { Get-Content -LiteralPath $log }
    $gates.Add([pscustomobject]@{name=$Name; exit_code=$code; evidence=$log})
    if ($code -ne 0) { throw "$Name failed with exit=$code; see $log" }
}

try {
    if (-not $SkipDependencyInstall) {
        $stage='DEPENDENCIES'
        Invoke-Gate 'dependencies' @('-m','pip','install','-r','requirements-phase4.txt')
    }
    $stage='PIP_CHECK'
    Invoke-Gate 'pip_check' @('-m','pip','check')
    $stage='COMPLETE_BACKEND_PYTEST'
    Invoke-Gate 'backend_pytest' @('-m','pytest','-q')

    if ($ForecastRoot) {
        $stage='EXISTING_FORECAST_VERIFY'
        $forecast = (Resolve-Path $ForecastRoot).Path
        Invoke-Gate 'forecast_verify' @('-m','app.forecasting.cli','verify','--out',$forecast)
    } else {
        $stage='PHASE3_FORECAST_FOR_SMALL_INPUT'
        $forecast = Join-Path $root 'forecast_run'
        Invoke-Gate 'forecast_train_evaluate' @('-m','app.forecasting.cli','train-evaluate',
            '--data',$data,'--out',$forecast,'--max-pairs',"$MaxPairs")
        Invoke-Gate 'forecast_verify' @('-m','app.forecasting.cli','verify','--out',$forecast)
    }

    $stage='REPLENISHMENT_RUN'
    $decision = Join-Path $root 'replenishment_run'
    Invoke-Gate 'replenishment_run' @('-m','app.replenishment.cli','run',
        '--data',$data,'--forecast',$forecast,'--out',$decision,'--max-pairs',"$MaxPairs")
    $stage='REPLENISHMENT_VALIDATION'
    Invoke-Gate 'replenishment_verify' @('-m','app.replenishment.cli','verify','--out',$decision)

    $stage='SAME_INPUT_REPRODUCIBILITY'
    $replay = Join-Path $root 'replenishment_replay'
    Invoke-Gate 'replenishment_reproduce' @('-m','app.replenishment.cli','reproduce',
        '--data',$data,'--forecast',$forecast,'--out',$replay,'--max-pairs',"$MaxPairs")
    $comparison = Get-Content (Join-Path $replay 'reproducibility.json') -Raw | ConvertFrom-Json
    if ($comparison.status -ne 'PASS' -or @($comparison.differences).Count -gt 0) {
        throw 'Non-deterministic replenishment output'
    }
    $manifest = Get-Content (Join-Path $decision 'replenishment_manifest.json') -Raw | ConvertFrom-Json
    $summary = Get-Content (Join-Path $decision 'summary.json') -Raw | ConvertFrom-Json
    if ($manifest.database_mutations -ne $false -or $manifest.full_scale_execution -ne 'DEFERRED') {
        throw 'Database/full-scale isolation invariant violated'
    }
    if ($summary.decisions -lt 1 -or ($summary.positive + $summary.blocked) -lt 1) {
        throw 'Missing meaningful replenishment outcomes'
    }
    $status='PASS'
}
catch {
    $failure=$_.Exception.Message
    Write-Host "`nSTOP | $stage | $failure" -ForegroundColor Red
}
finally {
    $report=[ordered]@{
        task='P1-T04'; phase='Replenishment Decision Engine';
        environment='candidate-Windows-local'; status=$status;
        date=(Get-Date).ToString('o');
        source_data=$data; forecast_root=$(if ($forecast) {$forecast} else {$null});
        failed_stage=$(if ($status -eq 'PASS') {$null} else {$stage});
        failure=$failure;
        full_scale_execution='DEFERRED_NOT_EXECUTED';
        database_mutations=$false;
        stages=@($gates.ToArray());
        personal_review='PENDING'
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $root 'P1-T04-local-verification.json')
    Write-Host "`n=== P1-T04 FINAL STATUS: $status ==="
    Write-Host "Evidence: $root"
}
if ($status -ne 'PASS') { exit 1 }
