<#
Launch local Phase 5 FastAPI and React using a single PowerShell terminal.
Never writes credentials to files. Never runs migrations or full-scale jobs.
Requires existing PostgreSQL 0005, Python venv and installed frontend node_modules.
#>
param([switch]$AllowSmallImport)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $MyInvocation.MyCommand.Path
$backend=Join-Path $project 'backend'
$frontend=Join-Path $project 'frontend'
$python=Join-Path $backend '.venv\Scripts\python.exe'
if(-not(Test-Path $python)){throw 'Backend .venv missing. Keep your existing installed Windows .venv.'}
if(-not(Test-Path (Join-Path $frontend 'node_modules\vite'))){throw 'Frontend node_modules missing. In frontend run npm install --no-audit --no-fund first.'}
foreach($port in @(8000,5173)){
    $listener=Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if($listener){throw "Port $port is already in use. Stop existing FastAPI/Vite terminals before launching."}
}
# Read settings from backend/.env when no explicit environment override exists.
# Never construct a URL pointing at the original `yottaflex` database.
if(-not $env:DATABASE_URL -and -not(Test-Path (Join-Path $backend '.env'))){
    throw 'Configure backend/.env (or set DATABASE_URL explicitly) for existing yottaflex_dual_v7. Original yottaflex database will not be used.'
}
$env:YOTTA_PROJECT_ROOT=$project
$env:PYTHONPATH=$backend
$env:YOTTA_MAX_PAIRS='36'
$env:YOTTA_DATA_ROOT=Join-Path $project 'examples\smoke_run'
$env:YOTTA_ARTIFACTS_ROOT=Join-Path $project 'evidence\phase5_runs'
$env:YOTTA_ALLOW_IMPORT=if($AllowSmallImport){'true'}else{'false'}
# Fail closed before starting anything unless this is the approved isolated database.
$check=@'
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(os.environ['YOTTA_PROJECT_ROOT']) / 'backend' / '.env',override=False)
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
u=make_url(os.environ.get('DATABASE_URL',''))
assert u.drivername=='postgresql+psycopg' and u.database=='yottaflex_dual_v7' and u.username=='yottaflex' and u.host in ('localhost','127.0.0.1') and (u.port or 5432)==5432,'Unexpected database target'
with create_engine(u,pool_pre_ping=True).connect() as c:
    assert c.execute(text('SELECT current_database()')).scalar_one()=='yottaflex_dual_v7','Unexpected database'
    assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0005','Apply the approved 0005 migration first'
print('PASS: existing local PostgreSQL migration 0005')
'@
$check | & $python -c 'import sys;exec(sys.stdin.read())'
if($LASTEXITCODE -ne 0){throw 'Database verification failed; servers not started'}
$logs=Join-Path $project ('evidence\phase5_local_'+(Get-Date -Format yyyyMMdd_HHmmss))
New-Item -ItemType Directory -Force -Path $logs | Out-Null
# Launch processes with inherited environment. Secrets do NOT appear in process arguments or files.
$back=Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.integration.api:create_app','--factory','--host','127.0.0.1','--port','8000') -WorkingDirectory $backend -PassThru -RedirectStandardOutput (Join-Path $logs 'backend.out.log') -RedirectStandardError (Join-Path $logs 'backend.err.log')
$npm=Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm run dev') -WorkingDirectory $frontend -PassThru -RedirectStandardOutput (Join-Path $logs 'frontend.out.log') -RedirectStandardError (Join-Path $logs 'frontend.err.log')
$healthy=$false
for($i=0;$i -lt 40;$i++){
    if($back.HasExited){break}
    try{
        $h=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
        if($h.status -eq 'ok'){$healthy=$true;break}
    }catch{}
    Start-Sleep -Milliseconds 500
}
if(-not $healthy){
    Write-Host "FastAPI failed to start; inspect $logs\backend.err.log" -ForegroundColor Red
    if(-not $npm.HasExited){Stop-Process -Id $npm.Id -Force -ErrorAction SilentlyContinue}
    throw 'API startup failed; no false ready status'
}
Write-Host 'PASS: FastAPI is healthy; trusted local demo needs no token.' -ForegroundColor Green
Write-Host 'Dashboard: http://127.0.0.1:5173' -ForegroundColor Cyan
Write-Host "Backend PID: $($back.Id); frontend launcher PID: $($npm.Id)"
Write-Host 'Open the welcome page and select Operations Manager or Developer.'
Write-Host "Server logs: $logs"
Write-Host 'Import remains disabled by default; only -AllowSmallImport enables bounded source import.'
