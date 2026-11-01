<#
Consolidated local P1-T02 verification. Start from existing backend directory.
No PostgreSQL connections, migrations, or files overwritten. Creates new
unique timestamped evidence and sample directories under ../evidence.
Requires existing venv. For -Full, install requirements-phase2.txt first.
#>
param([switch]$Full)
$ErrorActionPreference='Stop'
$python=(Resolve-Path '.\.venv\Scripts\python.exe').Path
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$root=Join-Path (Resolve-Path '..\evidence').Path ('P1-T02-'+$stamp)
New-Item -ItemType Directory -Path $root -ErrorAction Stop | Out-Null
$stages=[System.Collections.Generic.List[object]]::new()
function Run-Stage([string]$stage,[string[]]$argv) {
    $log=Join-Path $root ($stage+'.txt')
    Write-Host "`n=== $stage ===" -ForegroundColor Cyan
    & $python @argv *> $log
    $rc=$LASTEXITCODE
    Get-Content $log
    $stages.Add([pscustomobject]@{stage=$stage;exit_code=$rc;log=$log})
    if($rc -ne 0) { throw "$stage FAILED (exit $rc); stop. See $log" }
}
try {
    Run-Stage 'pytest' @('-m','pytest','-q')
    Run-Stage 'smoke' @('-m','app.generation.cli','generate','--profile','smoke','--out',(Join-Path $root 'smoke_run'))
    Run-Stage 'repro' @('-m','app.generation.cli','reproduce','--profile','smoke','--out',(Join-Path $root 'repro'))
    if($Full) {
        Run-Stage 'full' @('-m','app.generation.cli','generate','--profile','full','--out',(Join-Path $root 'full_run'))
    }
    $status='PASS'
} catch {
    $status='FAIL'
    Write-Host "STOP: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    [pscustomobject]@{
        task='P1-T02';environment='candidate-windows-local';status=$status
        scope=$(if($Full){'full + smoke'}else{'smoke only'})
        generated=(Get-Date).ToString('o');stages=$stages.ToArray()
        postgres_changes=$false;full_scale_verified=$Full -and $status -eq 'PASS'
    } | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $root 'local_verification.json')
    Write-Host "`nEvidence: $root"
    Write-Host "Overall: $status"
}
if($status -ne 'PASS') { exit 1 }
