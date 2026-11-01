# YottaFlex Enhanced Release — safe, repeatable local verification.
# This is a verifier only: no generator, DB migration, import, reset or Git push.
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$CheckPostgres,
    [switch]$RunBrowser
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Backend Python missing. Create backend\.venv and install backend\requirements-phase5.txt first.'
}
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$evidence = Join-Path $root ("evidence\final_local_$stamp")
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$results = New-Object 'System.Collections.Generic.List[object]'
$env:PYTHONPATH = $backend
$env:GIT_PAGER = 'cat'

function Invoke-Verification {
    param([string]$Name, [string]$Directory, [string]$Executable, [string[]]$Arguments)
    $log = Join-Path $evidence ($Name + '.log')
    Write-Host "`n===== $Name =====" -ForegroundColor Cyan
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments 2>&1 | Tee-Object -FilePath $log
        $statusCode = $LASTEXITCODE
    } catch {
        $_ | Out-String | Add-Content -LiteralPath $log
        $statusCode = 1
    } finally {
        Pop-Location
    }
    $status = if ($statusCode -eq 0) { 'PASS' } else { 'FAIL' }
    $results.Add([pscustomobject]@{name=$Name; status=$status; exit_code=$statusCode; log=(Split-Path $log -Leaf)})
    Write-Host "$Name : $status" -ForegroundColor $(if ($status -eq 'PASS') {'Green'} else {'Red'})
}

# Mutually exclusive file groups. Each of the 16 test modules is exercised exactly once.
$batches = @(
    @{ name='backend_A'; files=@('test_phase2_generation.py','test_phase2_statistics.py','test_phase2_full_gate.py','test_phase3_forecasting.py','test_contracts.py','test_backend_hardening.py','test_final_upgrade.py','test_step7_design.py') },
    @{ name='backend_B1'; files=@('test_phase4_replenishment.py','test_v5_inventory_contract.py') },
    @{ name='backend_B2'; files=@('test_v6_business_correctness.py','test_v7_adversarial.py','test_v8_scope_and_performance.py') },
    @{ name='backend_C'; files=@('test_phase5_integration.py','test_dual_dashboard.py','test_final_integrated_improvements.py') }
)
$all = @($batches | ForEach-Object { $_.files })
$existing = @(Get-ChildItem -LiteralPath (Join-Path $backend 'tests') -Filter 'test_*.py' -File | ForEach-Object Name)
if (@($all | Sort-Object -Unique).Count -ne $all.Count -or
    (@($existing | Sort-Object) -join '|') -ne (@($all | Sort-Object) -join '|')) {
    throw 'Backend batch membership does not exactly cover all test modules; aborting before testing.'
}
foreach ($batch in $batches) {
    $files = @($batch.files | ForEach-Object { "tests/$_" })
    Invoke-Verification $batch.name $backend $python (@('-m','pytest') + $files + @('-q','--tb=short'))
}

if ($CheckPostgres) {
    # Read-only script; DB URL is loaded from the local .env, never logged explicitly.
    Invoke-Verification 'postgres_schema_readonly' $backend $python @('verify_backend_schema_readonly.py')
} else {
    $results.Add([pscustomobject]@{name='postgres_schema_readonly';status='NOT_RUN';exit_code=$null;log=$null})
}

if ($SkipFrontend) {
    foreach ($name in @('frontend_vitest','frontend_build')) {
        $results.Add([pscustomobject]@{name=$name;status='NOT_RUN';exit_code=$null;log=$null})
    }
} else {
    if (-not (Test-Path (Join-Path $frontend 'node_modules\typescript\bin\tsc'))) {
        Write-Host 'Frontend node_modules missing. Run npm ci under frontend first.' -ForegroundColor Yellow
        foreach ($name in @('frontend_vitest','frontend_build')) {
            $results.Add([pscustomobject]@{name=$name;status='BLOCKED';exit_code=$null;log=$null})
        }
    } else {
        Invoke-Verification 'frontend_vitest' $frontend 'npm.cmd' @('run','test')
        Invoke-Verification 'frontend_build' $frontend 'npm.cmd' @('run','build')
    }
}

if ($RunBrowser) {
    # Requires separately running API and Vite servers, plus installed Playwright browser.
    # Never starts a second API process against the live database.
    if ($SkipFrontend -or -not (Test-Path (Join-Path $frontend 'node_modules\@playwright\test'))) {
        $results.Add([pscustomobject]@{name='playwright_chromium';status='BLOCKED';exit_code=$null;log=$null})
    } else {
        Invoke-Verification 'playwright_chromium' $frontend 'npx.cmd' @('playwright','test','--project=chromium','--reporter=list')
    }
} else {
    $results.Add([pscustomobject]@{name='playwright_chromium';status='NOT_RUN';exit_code=$null;log=$null})
}

$report = [ordered]@{
    generated_at=(Get-Date).ToString('o')
    project_root=$root
    method='four disjoint Python processes; frontend optional; read-only DB check optional'
    full_scale_73m='NOT_RUN (explicit separate resource-gated acceptance)'
    github_push='NOT_RUN'
    results=@($results.ToArray())
    all_executed_pass=(@($results | Where-Object { $_.status -eq 'FAIL' }).Count -eq 0)
}
$reportFile = Join-Path $evidence 'verification_summary.json'
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportFile -Encoding UTF8
Write-Host "`nLocal verification report: $reportFile" -ForegroundColor Cyan
if (@($results | Where-Object { $_.status -eq 'FAIL' }).Count -gt 0) {
    throw 'One or more executed checks failed; see verification_summary.json (not a full-scale acceptance).'
}
Write-Host 'Executed checks completed without failures. NOT_RUN/BLOCKED checks are not PASS.' -ForegroundColor Green
