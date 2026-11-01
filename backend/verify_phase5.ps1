<#
YottaFlex P1-T05 consolidated Windows local acceptance.
The existing backend directory and PostgreSQL role/database are preserved.
Migration requires -ApplyMigration; real small-data ingestion requires -RunDbAcceptance.
NEVER runs the deferred 2,000 x 100 x 365 full-scale workload.
#>
param(
    [switch]$ApplyMigration,
    [switch]$RunDbAcceptance,
    [switch]$SkipDependencyInstall,
    [switch]$SkipFrontendInstall,
    [switch]$BrowserE2E
)
$ErrorActionPreference='Stop'
$stage='PRECHECK';$outcome='FAIL';$failure=$null
$gates=[System.Collections.Generic.List[object]]::new()
$root=(Resolve-Path '..').Path
$timestamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$logDir=Join-Path $root "evidence\P1-T05-$timestamp"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$python=(Resolve-Path '.\.venv\Scripts\python.exe').Path
$alembic=(Resolve-Path '.\.venv\Scripts\alembic.exe').Path
$previousPGPassword=$env:PGPASSWORD
function Gate([string]$name,[string]$exe,[string[]]$argv){
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    $log=Join-Path $logDir "$name.log"
    $old=$ErrorActionPreference;$ErrorActionPreference='Continue'
    try {& $exe @argv *> $log;$code=$LASTEXITCODE}
    finally {$ErrorActionPreference=$old}
    if(Test-Path $log){Get-Content $log}
    $gates.Add([pscustomobject]@{name=$name;exit_code=$code;log=$log})
    if($code -ne 0){throw "$name failed (exit=$code); see $log"}
}
try {
    $stage='CONFIG'
    if(-not $env:DATABASE_URL){throw 'DATABASE_URL missing'}
    $frontend=Join-Path $root 'frontend'
    if(-not(Test-Path (Join-Path $frontend 'package.json'))){throw 'frontend package.json missing'}
    if(-not(Test-Path '.\alembic\versions\0003_phase5_integration.py')){throw 'migration 0003 missing'}
    if(-not(Test-Path '.\verify_phase5_postgres.py')){throw 'PostgreSQL acceptance file missing'}
    # Only use configured development instance; passwords remain in process memory.
    $env:YOTTA_DATA_ROOT=if($env:YOTTA_DATA_ROOT){$env:YOTTA_DATA_ROOT}else{Join-Path $root 'examples\smoke_run'}
    $env:YOTTA_ARTIFACTS_ROOT=if($env:YOTTA_ARTIFACTS_ROOT){$env:YOTTA_ARTIFACTS_ROOT}else{Join-Path $root 'evidence\phase5_runs'}
    $env:YOTTA_MAX_PAIRS='36'
    # Do not infer a full-scale dataset from folder contents; reject based on config.
    & $python -c 'from pathlib import Path; from app.generation.config import GeneratorConfig; from app.integration.settings import settings,validate_settings; s=settings();validate_settings(s);c=GeneratorConfig.from_json(s["data_root"]/"generator_config.json");assert c.sku_count<=100 and c.store_count<=10 and c.days<=90,"Full-scale deferred";print("SMALL-SAMPLE-ONLY | PASS")'
    if($LASTEXITCODE -ne 0){throw 'Small sample/config check failed'}

    $stage='DEPENDENCIES'
    if(-not $SkipDependencyInstall){Gate 'pip_install' $python @('-m','pip','install','-r','requirements-phase5.txt')}
    Gate 'pip_check' $python @('-m','pip','check')
    $stage='PYTEST'
    Gate 'backend_pytest' $python @('-m','pytest','-q')
    $stage='FRONTEND'
    Push-Location $frontend
    try {
        if(-not $SkipFrontendInstall){Gate 'frontend_npm_install' 'npm.cmd' @('install','--no-audit','--no-fund')}
        Gate 'frontend_build' 'npm.cmd' @('run','build')
        Gate 'frontend_tests' 'npm.cmd' @('run','test')
    }
    finally{Pop-Location}

    if($RunDbAcceptance -and -not $ApplyMigration){throw '-RunDbAcceptance requires -ApplyMigration for verified migration gate'}
    if($ApplyMigration){
        $stage='DATABASE_BACKUP'
        $bin='C:\Program Files\PostgreSQL\18\bin'
        $dump=Join-Path $bin 'pg_dump.exe';$restore=Join-Path $bin 'pg_restore.exe'
        if(-not(Test-Path $dump) -or -not(Test-Path $restore)){throw 'PostgreSQL 18 dump/restore tools missing'}
        $target=& $python -c 'import os;from sqlalchemy.engine import make_url;u=make_url(os.environ["DATABASE_URL"]);print("|".join([u.drivername,u.host or "",str(u.port or 5432),u.database or "",u.username or ""]))'
        if($LASTEXITCODE -ne 0 -or $target -ne 'postgresql+psycopg|localhost|5432|yottaflex|yottaflex'){
            throw 'Refusing to back up or migrate an unexpected DB target (expected local yottaflex)'
        }
        $env:PGPASSWORD=& $python -c 'import os;from sqlalchemy.engine import make_url;print(make_url(os.environ["DATABASE_URL"]).password or "")'
        if($LASTEXITCODE -ne 0){throw 'Cannot resolve DB authentication'}
        $backup=Join-Path $logDir "yottaflex-pre-0003-$timestamp.dump"
        Gate 'pg_dump' $dump @('-w','-h','localhost','-p','5432','-U','yottaflex','-d','yottaflex','--format=custom','--no-owner','--no-acl',"--file=$backup")
        if((Get-Item $backup).Length -lt 2048){throw 'Backup unexpectedly small'}
        Gate 'pg_restore_toc' $restore @('--list',$backup)
        $toc=Get-Content (Join-Path $logDir 'pg_restore_toc.log') -Raw
        foreach($table in @('sku','forecast','purchase_recommendation','tech_inventory_movement_event')){
            if($toc -notmatch "TABLE public $table(\s|`r|`n)"){throw "Backup TOC missing table: $table"}
        }
        Gate 'pg_restore_decode' $restore @('--file=NUL',$backup)
        $hash=(Get-FileHash -Algorithm SHA256 $backup).Hash
        Set-Content (Join-Path $logDir 'backup_sha256.txt') "$backup`nSHA256=$hash"

        $stage='MIGRATION_0003'
        Gate 'alembic_upgrade_0003' $alembic @('upgrade','0003')
        Gate 'alembic_current' $alembic @('current')
        $current=Get-Content (Join-Path $logDir 'alembic_current.log') -Raw
        if($current -notmatch '0003 \(head\)'){throw 'Alembic current not 0003 (head)'}

        $stage='PG_SCHEMA'
        Gate 'postgres_schema_0003' $python @('.\verify_phase5_schema.py')
    }
    if($RunDbAcceptance){
        $stage='POSTGRESQL_END_TO_END'
        $env:YOTTA_ALLOW_IMPORT='true'
        $env:YOTTA_PHASE5_REPORT=Join-Path $logDir 'postgres_acceptance.json'
        Gate 'postgres_end_to_end' $python @('.\verify_phase5_postgres.py')
        $report=Get-Content $env:YOTTA_PHASE5_REPORT -Raw | ConvertFrom-Json
        if($report.status -ne 'PASS'){throw 'PostgreSQL acceptance report did not pass'}
    }
    if($BrowserE2E){
        if(-not $RunDbAcceptance){throw '-BrowserE2E requires -RunDbAcceptance and -ApplyMigration'}
        $stage='BROWSER_END_TO_END'
        Push-Location $frontend
        try{
            Gate 'playwright_install_chromium' 'npm.cmd' @('exec','playwright','install','chromium')
            Gate 'browser_e2e' 'npm.cmd' @('run','test:e2e')
        }
        finally{Pop-Location}
    }
    $outcome=if($RunDbAcceptance){'TECHNICAL_PASS_PENDING_PERSONAL_UI_REVIEW'}elseif($ApplyMigration){'MIGRATION_PASS_E2E_PENDING'}else{'CODE_TEST_PASS_POSTGRES_PENDING'}
}
catch{
    $failure=$_.Exception.Message
    Write-Host "`nSTOP | $stage | $failure" -ForegroundColor Red
}
finally{
    if($null -eq $previousPGPassword){Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue}else{$env:PGPASSWORD=$previousPGPassword}
    $report=[ordered]@{task='P1-T05';status=$outcome;failure=$failure;stopped_at=$(if($failure){$stage}else{$null});time=(Get-Date).ToString('o');gates=@($gates.ToArray());full_scale_test='DEFERRED';personal_signoff='PENDING'}
    $report | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $logDir 'phase5_summary.json')
    Write-Host "`nP1-T05 | $outcome | evidence: $logDir"
}
if($failure){throw 'Phase 5 verification stopped at failed gate; inspect evidence before retry'}
