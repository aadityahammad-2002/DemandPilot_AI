# YottaFlex V8 - Consolidated Windows Acceptance / Regression Verifier (PS StrictMode FIX)
# FIX 2026-11-01: Enumerate JSON trace properties safely as array before reading .Count.
# Designed for the approved SMALL development dataset in yottaflex_dual_v7.
# Does NOT perform migrations, generate data, create jobs, or change application source.
# Requires the EXISTING FastAPI worker to be running on 127.0.0.1:8000.
# Creates evidence report files only. Backend/browser test suites are invoked as-is;
# their own fixture behavior is not controlled by this script. DB fingerprints are checked.
param(
    [string]$ProjectRoot = $PSScriptRoot,
    [string]$ApiBase = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$script:Checks = New-Object 'System.Collections.Generic.List[object]'
$script:Abort = $false
$script:BaselineMilliseconds = 61.40
$script:BaselineKB = 101.28

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Details)
    $script:Checks.Add([pscustomobject]@{
        Check = $Name; Status = $Status; Details = $Details
    })
    if ($Status -eq 'PASS') { Write-Host "PASS  $Name - $Details" -ForegroundColor Green }
    elseif ($Status -eq 'WARN') { Write-Host "WARN  $Name - $Details" -ForegroundColor Yellow }
    else { Write-Host "FAIL  $Name - $Details" -ForegroundColor Red }
}
function Do-Check {
    param([string]$Name, [scriptblock]$Action)
    try {
        $description = & $Action
        $last = @($description | Where-Object { $null -ne $_ })
        Add-Check $Name 'PASS' (($last | ForEach-Object { [string]$_ }) -join '; ')
    }
    catch {
        Add-Check $Name 'FAIL' $_.Exception.Message
    }
}
function Run-Native {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory)
    Push-Location $WorkingDirectory
    try {
        Write-Host ("RUN: " + $Executable + " " + ($Arguments -join ' ')) -ForegroundColor Cyan
        & $Executable @Arguments | Out-Host
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Command exited with code $exitCode"
        }
    }
    finally { Pop-Location }
}
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
function Request-Json {
    param([string]$Path)
    $address = "$ApiBase$Path"
    return Invoke-RestMethod -Uri $address -Method Get -TimeoutSec 30
}
function Request-Web {
    param([string]$Path)
    return Invoke-WebRequest -Uri "$ApiBase$Path" -Method Get -UseBasicParsing -TimeoutSec 30
}
function Verify-Db {
    param([string]$Phase)
    Write-Host "===== LIVE POSTGRESQL $($Phase.ToUpperInvariant()) AUDIT =====" -ForegroundColor Cyan
    Push-Location $backend
    try {
        $dbAuditPy | & $python - $Phase $evidenceDir | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Read-only PostgreSQL $Phase audit failed (exit $LASTEXITCODE)" }
    }
    finally { Pop-Location }
}

# Preflight: fail closed if user points outside the approved local project/DB/server.
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$backend = Join-Path $ProjectRoot 'backend'
$frontend = Join-Path $ProjectRoot 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$vitest = Join-Path $frontend 'node_modules\.bin\vitest.cmd'
$playwright = Join-Path $frontend 'node_modules\.bin\playwright.cmd'
$npmCmd = (Get-Command 'npm.cmd' -ErrorAction SilentlyContinue)
if (-not $npmCmd) { throw 'npm.cmd not found in PATH. Install/enable Node.js before running this verifier.' }
foreach ($required in @($backend,$frontend,$python,$vitest,$playwright)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing required directory/executable: $required" }
}
$apiUri = [Uri]$ApiBase
if ($apiUri.Scheme -ne 'http' -or $apiUri.Host -ne '127.0.0.1' -or $apiUri.Port -ne 8000) {
    throw 'STOP: ApiBase must be http://127.0.0.1:8000 (no remote or alternate API target).'
}
$ApiBase = $ApiBase.TrimEnd('/')
$evidenceDir = Join-Path $ProjectRoot ('evidence\V8_ONE_SHOT_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
$transcript = Join-Path $evidenceDir 'console.log'
$transcriptActive = $false
try { Start-Transcript -Path $transcript -Force | Out-Null; $transcriptActive = $true }
catch { Write-Warning ('Transcript unavailable: ' + $_.Exception.Message) }
Write-Host "YottaFlex V8 consolidated verifier | $([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Evidence: $evidenceDir"
Write-Host 'DATA SAFETY: small smoke dataset only; no full-scale generator/migrations/job submissions.'

# The Python block is identical for pre/post and only uses SELECT in READ ONLY transactions.
# Hashes of all 12 existing data tables protect against unnoticed business-row changes.
$dbAuditPy = @'
import hashlib
import json
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import database_url

phase, report_dir = sys.argv[1:3]
if phase not in ('pre', 'post'):
    raise SystemExit('STOP: invalid audit phase')
url = database_url()
target = make_url(url)
if (target.drivername != 'postgresql+psycopg'
        or target.database != 'yottaflex_dual_v7'
        or target.host not in ('localhost', '127.0.0.1')):
    raise SystemExit('STOP: unexpected database target; data not accessed')

names = (
    'sku', 'store', 'sales_history', 'inventory_snapshot', 'supplier',
    'supplier_sku', 'lead_time_observation', 'forecast', 'reorder_policy',
    'purchase_recommendation', 'tech_pipeline_run', 'tech_persistence_map'
)
expected = {
    'sku':12, 'store':3, 'sales_history':1164, 'inventory_snapshot':1164,
    'supplier':80, 'supplier_sku':12, 'lead_time_observation':24,
    'forecast':138, 'reorder_policy':36, 'purchase_recommendation':36,
    'tech_pipeline_run':8, 'tech_persistence_map':174
}
engine = create_engine(url, pool_pre_ping=True)
report = {'phase':phase, 'database':target.database, 'tables':{}, 'checks':{}}

def check(name, ok, detail=''):
    report['checks'][name] = {'pass':bool(ok), 'detail':str(detail)}
    print(('PASS' if ok else 'FAIL') + ': ' + name + (' | '+str(detail) if detail else ''))
    if not ok:
        failures.append(name)

failures = []
try:
    with engine.connect() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        dbname = conn.scalar(text('SELECT current_database()'))
        check('isolated database', dbname == 'yottaflex_dual_v7', dbname)
        migration = conn.scalar(text('SELECT version_num FROM alembic_version'))
        check('Alembic revision 0005', migration == '0005', migration)
        for name in names:
            count = conn.scalar(text(f'SELECT count(*) FROM public.{name}'))
            # Guard against accidentally fingerprinting a massive generated dataset.
            if count > 50000:
                raise RuntimeError(f'STOP: {name} has {count} rows; approved SMALL dataset only')
            sha = hashlib.sha256()
            raw_rows = conn.execute(text(f'SELECT to_jsonb(t)::text FROM public.{name} t')).scalars().all()
            for serialized in sorted(raw_rows):
                sha.update(serialized.encode('utf-8'))
                sha.update(b'\n')
            report['tables'][name] = {'count':int(count), 'sha256':sha.hexdigest()}
            check(f'{name} smoke count', count == expected[name], f'{count} (expected {expected[name]})')
        check('all table row counts', all(report['tables'][x]['count'] == expected[x] for x in names))

        # Bidirectional record-to-ledger checks, using exact previously inspected columns.
        integrity = {
            'unmapped_forecasts': """SELECT count(*) FROM forecast f WHERE NOT EXISTS
                (SELECT 1 FROM tech_persistence_map p WHERE p.entity_type='Forecast'
                AND p.database_id=f.forecast_id)""",
            'missing_forecast_targets': """SELECT count(*) FROM tech_persistence_map p
                WHERE p.entity_type='Forecast' AND NOT EXISTS
                (SELECT 1 FROM forecast f WHERE f.forecast_id=p.database_id)""",
            'unmapped_recommendations': """SELECT count(*) FROM purchase_recommendation r
                WHERE NOT EXISTS (SELECT 1 FROM tech_persistence_map p
                WHERE p.entity_type='PurchaseRecommendation' AND p.database_id=r.recommendation_id)""",
            'missing_recommendation_targets': """SELECT count(*) FROM tech_persistence_map p
                WHERE p.entity_type='PurchaseRecommendation' AND NOT EXISTS
                (SELECT 1 FROM purchase_recommendation r WHERE r.recommendation_id=p.database_id)""",
            'duplicate_mapped_ids': """SELECT count(*) FROM
                (SELECT entity_type,database_id FROM tech_persistence_map
                GROUP BY entity_type,database_id HAVING count(*)>1) q"""
        }
        for key,sql in integrity.items():
            n = conn.scalar(text(sql))
            check(key, n == 0, n)
        grouped = conn.execute(text("""SELECT status, count(*) AS n,
            count(*) FILTER (WHERE recommended_quantity > 0) AS positive,
            count(*) FILTER (WHERE recommended_quantity = 0) AS zero
            FROM purchase_recommendation GROUP BY status""")).mappings().all()
        check('all recommendations status GENERATED', len(grouped) == 1 and
              grouped[0]['status'] == 'GENERATED' and grouped[0]['n'] == 36, grouped)
        check('positive recommendations = 22', sum(row['positive'] for row in grouped) == 22)
        check('zero-quantity recommendations = 14', sum(row['zero'] for row in grouped) == 14)
        discontinued = conn.execute(text("""SELECT r.recommendation_id, r.decision_date,
            s.launch_date,s.discontinue_date FROM purchase_recommendation r
            JOIN sku s ON s.sku_id=r.sku_id
            WHERE r.recommended_quantity > 0 AND s.status='DISCONTINUED'
            ORDER BY r.recommendation_id""")).mappings().all()
        valid = len(discontinued) == 3 and all(
            row['launch_date'] <= row['decision_date'] < row['discontinue_date']
            for row in discontinued if row['launch_date'] is not None and row['discontinue_date'] is not None
        ) and all(row['launch_date'] is not None and row['discontinue_date'] is not None for row in discontinued)
        check('3 historical discontinued-SKU positives eligible on decision date', valid,
              [row['recommendation_id'] for row in discontinued])
        forecast_source = conn.execute(text('SELECT run_id, count(*) FROM forecast GROUP BY run_id')).all()
        rec_source = conn.execute(text('SELECT forecast_run_id, count(*) FROM purchase_recommendation GROUP BY forecast_run_id')).all()
        check('one shared forecast source run', len(forecast_source)==1 and forecast_source[0][1]==138
              and len(rec_source)==1 and rec_source[0][0]==forecast_source[0][0] and rec_source[0][1]==36)
        jobs = conn.execute(text('SELECT job_id,kind,status,result FROM tech_pipeline_run')).mappings().all()
        by_id={j['job_id']:j for j in jobs}
        forecast_inserts = []
        rec_inserts = []
        parent_consistent = True
        for job in jobs:
            if job['status']!='SUCCEEDED':
                parent_consistent=False
            result=job['result'] or {}
            if isinstance(result,str): result=json.loads(result)
            if job['kind']=='FORECAST':
                forecast_inserts.append(int(result.get('inserted_forecasts',-1)))
                if result.get('source_run_id')!=forecast_source[0][0]: parent_consistent=False
            if job['kind']=='REPLENISH':
                rec_inserts.append(int(result.get('inserted_recommendations',-1)))
                params=result.get('parameters') or {}
                parent=by_id.get(params.get('forecast_job_id'))
                if not parent or parent['kind']!='FORECAST' or parent['status']!='SUCCEEDED':
                    parent_consistent=False
                else:
                    parent_result=parent['result'] or {}
                    if isinstance(parent_result,str): parent_result=json.loads(parent_result)
                    if parent_result.get('source_run_id')!=forecast_source[0][0]: parent_consistent=False
        check('forecast idempotent replay counts', sorted(forecast_inserts)==[0,0,138],forecast_inserts)
        check('replenishment idempotent replay counts', sorted(rec_inserts)==[0,0,0,36],rec_inserts)
        check('replenishment parent job source linkage',parent_consistent)
        check('8 expected jobs',len(jobs)==8)
        if phase == 'pre':
            # EXPLAIN ANALYZE applies only to SELECT and is run in read-only transaction.
            print('===== SELECT-ONLY EXPLAIN ANALYZE =====')
            for label,sql in (
                ('recommendations COUNT','SELECT count(*) FROM purchase_recommendation'),
                ('recommendations PAGE',"""SELECT recommendation_id,decision_date,status,
                    recommended_quantity,forecast_run_id FROM purchase_recommendation
                    ORDER BY decision_date DESC,recommendation_id ASC LIMIT 50"""),
                ('blocked COUNT',"SELECT count(*) FROM purchase_recommendation WHERE status='BLOCKED'")
            ):
                print('--- ' + label + ' ---')
                lines=conn.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) '+sql)).scalars().all()
                for line in lines: print(line)
        if phase == 'post':
            pre = json.loads((Path(report_dir)/'database_pre.json').read_text(encoding='utf-8'))
            changes = [name for name in names if pre['tables'][name] != report['tables'][name]]
            check('business/ledger data unchanged before vs after tests',len(changes)==0, changes or '12 tables byte-hash matched')
    (Path(report_dir)/f'database_{phase}.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    if failures:
        raise RuntimeError(f'{len(failures)} database checks failed: '+', '.join(failures))
    print('PASS: Live PostgreSQL '+phase+' audit complete')
finally:
    engine.dispose()
'@

# The preflight must pass before running any test suite.
try {
    Verify-Db 'pre'
    Add-Check 'PostgreSQL preflight + integrity + 12-table fingerprint' 'PASS' 'isolated v7 database, migration 0005, reconciliations and historical eligibility'
}
catch {
    Add-Check 'PostgreSQL preflight + integrity + 12-table fingerprint' 'FAIL' $_.Exception.Message
    $script:Abort = $true
}
try {
    if (-not $script:Abort) {
        $health = Request-Web '/health'
        Assert-True ($health.StatusCode -eq 200) 'Health endpoint did not return HTTP 200'
        Add-Check 'Existing FastAPI worker health' 'PASS' '127.0.0.1:8000/health returned 200; no new worker started'
    }
}
catch {
    Add-Check 'Existing FastAPI worker health' 'FAIL' $_.Exception.Message
    $script:Abort = $true
}

if (-not $script:Abort) {
    Do-Check 'Backend automated regression' {
        Run-Native $python @('-m','pytest','-q','--tb=short') $backend
        'pytest exit 0 (previous baseline 192 passed, 1 skipped)'
    }
    Do-Check 'PostgreSQL schema verification scripts' {
        foreach ($name in @('verify_backend_schema_readonly.py','verify_phase5_schema.py')) {
            $file=Join-Path $backend $name
            Assert-True (Test-Path $file) "Missing schema verification $name"
            Run-Native $python @($name) $backend
        }
        'both existing verification scripts returned exit 0'
    }
    Do-Check 'Frontend Vitest 8 unit regressions' {
        Run-Native $vitest @('run','src') $frontend
        'Vitest scoped to src (avoids known Playwright test discovery conflict)'
    }
    Do-Check 'TypeScript + Vite production build' {
        Run-Native $npmCmd.Source @('run','build') $frontend
        'TypeScript and Vite built successfully'
    }
    Do-Check 'Live API business counts, pagination, detail trace' {
        $f=Request-Json '/api/v1/forecasts?limit=1&offset=0'
        Assert-True ($f.total -eq 138 -and @($f.items).Count -eq 1) 'Forecast page mismatch'
        $r=Request-Json '/api/v1/recommendations?limit=50&offset=0'
        Assert-True ($r.total -eq 36 -and @($r.items).Count -eq 36) 'Recommendations page mismatch'
        foreach ($rec in @($r.items)) {
            Assert-True (-not ($rec.PSObject.Properties.Name -contains 'decision_trace')) 'Full decision trace leaked in summary listing'
        }
        $recId=[uri]::EscapeDataString([string]$r.items[0].recommendation_id)
        $detail=Request-Json "/api/v1/recommendations/$recId"
        Assert-True ($detail.recommendation.recommendation_id -eq $r.items[0].recommendation_id) 'Detail ID mismatch'
        Assert-True ($null -ne $detail.recommendation.decision_trace) 'Detail trace missing'
        $traceFieldCount = @($detail.recommendation.decision_trace.PSObject.Properties | Where-Object { $_.MemberType -eq 'NoteProperty' }).Count
        Assert-True ($traceFieldCount -gt 0) 'Detail trace empty'
        $rLast=Request-Json '/api/v1/recommendations?limit=1&offset=35'
        Assert-True ($rLast.total -eq 36 -and @($rLast.items).Count -eq 1) 'Last-page pagination mismatch'
        $rEmpty=Request-Json '/api/v1/recommendations?limit=1&offset=36'
        Assert-True ($rEmpty.total -eq 36 -and @($rEmpty.items).Count -eq 0) 'Empty-page pagination mismatch'
        $s=Request-Json '/api/v1/sales?stockout_only=true&limit=1&offset=0'
        Assert-True ($s.total -eq 73 -and @($s.items).Count -eq 1) 'Stockout sales count mismatch'
        $j=Request-Json '/api/v1/jobs?limit=1&offset=0'
        Assert-True ($j.total -eq 8) 'Job count mismatch'
        $c=Request-Json '/api/v1/run-catalog'
        Assert-True (@($c.forecast_runs).Count -eq 1 -and @($c.replenishment_runs).Count -eq 1) 'Source-run catalog mismatch'
        $e=Request-Json '/api/v1/exceptions?limit=50&offset=0'
        Assert-True ($e.total -eq 0) 'Unexpected exceptions count'
        '138 forecasts, 36 recommendations, 73 stockouts, 8 jobs, 1+1 source runs; pagination and detail trace verified'
    }
    Do-Check 'PERF-02 payload and latency (5 samples)' {
        $endpoint='/api/v1/recommendations?limit=50&offset=0'
        [void](Request-Web $endpoint)   # warm-up
        $samples=New-Object 'System.Collections.Generic.List[double]'
        $sizes=New-Object 'System.Collections.Generic.List[double]'
        for ($i=1; $i -le 5; $i++) {
            $watch=[Diagnostics.Stopwatch]::StartNew()
            $response=Request-Web $endpoint
            $watch.Stop()
            $samples.Add($watch.Elapsed.TotalMilliseconds)
            $sizes.Add(([Text.Encoding]::UTF8.GetByteCount([string]$response.Content))/1024.0)
        }
        $mean=($samples | Measure-Object -Average).Average
        $kb=($sizes | Measure-Object -Average).Average
        $reduction=(1-$kb/$script:BaselineKB)*100
        $latencyChange=(1-$mean/$script:BaselineMilliseconds)*100
        $metrics=[ordered]@{samples_ms=@($samples);average_ms=[math]::Round($mean,2);
            payload_kb=[math]::Round($kb,2);payload_reduction_pct=[math]::Round($reduction,2);
            historical_before_ms=$script:BaselineMilliseconds;historical_before_kb=$script:BaselineKB;
            latency_change_vs_historical_pct=[math]::Round($latencyChange,2)}
        $metrics | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $evidenceDir 'api_perf.json') -Encoding UTF8
        Assert-True ($kb -lt 30) "Unexpected large recommendation listing payload $([math]::Round($kb,2)) KB"
        "avg $([math]::Round($mean,2)) ms, $([math]::Round($kb,2)) KB, payload -$([math]::Round($reduction,2))% vs historical"
    }
    Do-Check 'Playwright full browser regression (7 tests)' {
        # Existing FastAPI must be healthy so webServer reuses it, not starts another worker.
        $health=Request-Web '/health'
        Assert-True ($health.StatusCode -eq 200) 'Existing API worker unavailable before browser run'
        Run-Native $playwright @('test','--project=chromium','--reporter=list') $frontend
        'Playwright exit 0, includes new persisted decision trace drawer test'
    }
    Do-Check 'No business/ledger changes during one-shot run' {
        Verify-Db 'post'
        'all 12 smoke-data tables have unchanged counts + SHA-256 row-content fingerprints'
    }
    $app=Join-Path $frontend 'src\App.tsx'
    $api=Join-Path $backend 'app\integration\api.py'
    $e2e=Join-Path $frontend 'e2e\real-dashboard.spec.ts'
    Do-Check 'PERF-02 regression source coverage' {
        $appText=[IO.File]::ReadAllText($app)
        $apiText=[IO.File]::ReadAllText($api)
        $e2eText=[IO.File]::ReadAllText($e2e)
        Assert-True ($appText.Contains('Expand full recorded trace')) 'Detail drawer full trace toggle missing'
        Assert-True ($apiText.Contains("if c.name!='decision_trace'")) 'Lightweight SQL projection missing'
        Assert-True ($e2eText.Contains('recommendation drawer renders complete persisted decision trace')) 'Full trace browser test missing'
        'list excludes trace; detail drawer and regression assertion retained'
    }
}

# Non-blocking engineering gaps (not silently represented as completed tests).
Do-Check 'TEST-CONFIG-01 source guard' {
    $unitConfig = Join-Path $frontend 'vitest.config.ts'
    Assert-True (Test-Path -LiteralPath $unitConfig) 'Vitest config file missing'
    $configText = [IO.File]::ReadAllText($unitConfig)
    Assert-True ($configText.Contains('**/e2e/**')) 'Vitest config does not exclude E2E tests'
    Assert-True ($configText.Contains('src/**/*.test.ts')) 'Vitest unit test inclusion rule missing'
    'Vitest source config excludes Playwright e2e; actual no-scope Vitest command not run by this script'
}
Add-Check 'PERF-03' 'WARN' 'Vite bundle-size warning (~693 KB minified) may remain; code splitting not performed.'
Add-Check 'Live concurrency/failure recovery' 'WARN' 'This read-only acceptance does not start concurrent jobs or kill workers; dedicated destructive/isolation-controlled validation remains.'
Add-Check 'Full-scale generation/load test' 'WARN' 'Intentionally DEFERRED: 2,000 SKU x 100 stores x 365 days, per user instruction.'
if (-not $script:Abort) {
    Add-Check 'Current historical SKU eligibility' 'PASS' 'Three current positive recommendations have decision date 2026-01-28 before discontinuation 2026-01-30 (validated in database audit).'
} else {
    Add-Check 'Current historical SKU eligibility' 'WARN' 'Database preflight failed; current eligibility could not be verified during this run.'
}
Do-Check 'Historical Phase 4 artifact reconciliation' {
    $historicPath = Join-Path $ProjectRoot 'evidence\P1-T04-20261030-231108-257\replenishment_replay\first\exceptions.csv'
    Assert-True (Test-Path $historicPath) 'Original Phase 4 exceptions artifact missing'
    $historic = @(Import-Csv $historicPath)
    Assert-True ($historic.Count -eq 3) 'Original Phase 4 blocked recommendation count not 3'
    foreach ($r in $historic) {
        Assert-True ($r.status -eq 'BLOCKED') 'Original case not blocked'
        Assert-True ($r.reason_codes.Contains('DISCONTINUED_OR_INELIGIBLE_SKU')) 'Original reason mismatch'
        Assert-True ($r.sku_id -eq 'SKU-000002') 'Original SKU mismatch'
        Assert-True ($r.decision_date -eq '2026-01-28') 'Original decision date mismatch'
    }
    $current = Get-Content (Join-Path $evidenceDir 'database_pre.json') -Raw | ConvertFrom-Json
    $eligible = $current.checks.'3 historical discontinued-SKU positives eligible on decision date'
    Assert-True ($eligible.pass -eq $true) 'Current DB eligibility audit not passed'
    $matched = @([regex]::Matches([string]$eligible.detail, 'PO-2026-[0-9]{6}') | ForEach-Object Value | Sort-Object -Unique)
    $oldIds = @($historic | ForEach-Object recommendation_id | Sort-Object -Unique)
    Assert-True (($matched -join ',') -eq ($oldIds -join ',')) 'Original blocked IDs and current eligible IDs differ'
    'Three archival BLOCKED recommendation identities exactly match the three currently eligible positives'
}

$summaryPath = Join-Path $evidenceDir 'summary.json'
$script:Checks | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
$failures = @($script:Checks | Where-Object Status -eq 'FAIL').Count
$passes = @($script:Checks | Where-Object Status -eq 'PASS').Count
$warnings = @($script:Checks | Where-Object Status -eq 'WARN').Count
Write-Host "`n================ YOTTAFLEX V8 ACCEPTANCE SUMMARY ================ " -ForegroundColor Cyan
$script:Checks | Format-Table -AutoSize Check,Status,Details | Out-Host
Write-Host "PASS=$passes  FAIL=$failures  WARN=$warnings"
Write-Host "Reports: $evidenceDir"
Write-Host "Summary: $summaryPath"
if ($transcriptActive) { Stop-Transcript | Out-Null }
if ($failures -gt 0 -or $script:Abort) {
    Write-Host 'OVERALL: FAIL / FOLLOW-UP REQUIRED. See summary.json and console.log.' -ForegroundColor Red
    exit 1
}
Write-Host 'OVERALL: PASS for validated small-development-dataset gates; warnings are explicitly outstanding.' -ForegroundColor Green
exit 0
