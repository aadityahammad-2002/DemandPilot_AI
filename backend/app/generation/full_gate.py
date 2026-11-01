"""Safe full-size generator acceptance, no database writes and no source edits.

Runs a measured Parquet pilot, verifies disk/RAM headroom, generates full profile,
independently validates it, and regenerates logically without 2x full disk usage.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta, datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from app.generation.config import GeneratorConfig
from app.generation.generator import product_record
from app.generation.full_reproduce import compare_streaming

GIB=1024**3


def memory_bytes():
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_=[('dwLength',wintypes.DWORD),('dwMemoryLoad',wintypes.DWORD),
                     ('ullTotalPhys',ctypes.c_ulonglong),('ullAvailPhys',ctypes.c_ulonglong),
                     ('ullTotalPageFile',ctypes.c_ulonglong),('ullAvailPageFile',ctypes.c_ulonglong),
                     ('ullTotalVirtual',ctypes.c_ulonglong),('ullAvailVirtual',ctypes.c_ulonglong),
                     ('ullAvailExtendedVirtual',ctypes.c_ulonglong)]
        mem=MEMORYSTATUSEX()
        mem.dwLength=ctypes.sizeof(mem)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            raise OSError('GlobalMemoryStatusEx failed')
        return mem.ullTotalPhys,mem.ullAvailPhys
    try:
        import os
        total=int(os.sysconf('SC_PHYS_PAGES'))*int(os.sysconf('SC_PAGE_SIZE'))
        lines=Path('/proc/meminfo').read_text().splitlines()
        avail=int(next(line.split()[1] for line in lines if line.startswith('MemAvailable:')))*1024
        return total,avail
    except (FileNotFoundError,StopIteration,KeyError,ValueError):
        return None,None


def eligible_count(cfg):
    start=date.fromisoformat(cfg.start_date)
    days=0
    for n in range(1,cfg.sku_count+1):
        p=product_record(n,cfg,start)
        begin=max(start,date.fromisoformat(p['launch_date']))
        end=min(start+timedelta(days=cfg.days),date.fromisoformat(p['discontinue_date']) if p['discontinue_date'] else start+timedelta(days=cfg.days))
        days+=max(0,(end-begin).days)
    return days*cfg.store_count


def directory_bytes(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file())


def run_task(label, args, log_path, target_drive, *, stop_disk_gib=6, stop_ram_gib=0.75):
    print(f'START | {label}',flush=True)
    with log_path.open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([sys.executable,*args], stdout=log,stderr=subprocess.STDOUT)
        last_heartbeat=time.monotonic()
        while proc.poll() is None:
            time.sleep(15)
            free=shutil.disk_usage(target_drive).free
            _,available=memory_bytes()
            now=time.monotonic()
            if now-last_heartbeat >= 60:
                print(f'RUNNING | {label} | free_disk_gib={free/GIB:.2f} | free_ram_gib={(available/GIB if available is not None else -1):.2f}',flush=True)
                last_heartbeat=now
            if free < stop_disk_gib*GIB or (available is not None and available < stop_ram_gib*GIB):
                proc.kill();proc.wait()
                raise RuntimeError(f'{label} aborted by resource guard: free disk={free/GIB:.2f} GiB, available RAM={available/GIB if available is not None else "unknown"} GiB')
        if proc.returncode != 0:
            tail=log_path.read_text(encoding='utf-8',errors='replace')[-3500:]
            raise RuntimeError(f'{label} failed exit={proc.returncode}; tail: {tail}')
    print(f'PASS | {label} | log={log_path}',flush=True)


def run(root: Path, *, min_free_gib:float=18, min_ram_available_gib:float=2, stop_disk_gib:float=6) -> dict:
    root=root.resolve()
    if root.exists():
        raise FileExistsError(f'Output directory already exists: {root}. Use a new directory; no overwrites.')
    root.mkdir(parents=True)
    record={'task':'P1-T02 full acceptance','environment':'candidate-local-required',
            'status':'IN_PROGRESS','generated_at':datetime.now(timezone.utc).isoformat(),
            'stages':{},'source_database_modified':False,
            'schema_migrations_modified':False, 'phase3_started':False}
    def save():
        (root/'full_scale_acceptance.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    stage='PRECHECK'
    save()
    try:
        import pyarrow  # noqa: F401
        import duckdb  # noqa: F401
        cfg=GeneratorConfig()
        cfg.validate()
        if (cfg.sku_count,cfg.store_count,cfg.days,cfg.format,cfg.store_batch,cfg.movement_journal)!=(2000,100,365,'parquet',2,True):
            raise ValueError('Full config differs from approved 2000/100/365 Parquet + movement journal, store_batch=2')
        expected=eligible_count(cfg)
        free_before=shutil.disk_usage(root).free
        total,available=memory_bytes()
        if free_before < min_free_gib*GIB:
            raise RuntimeError(f'Insufficient free disk: {free_before/GIB:.2f} GiB; require {min_free_gib} GiB')
        if available is None or available < min_ram_available_gib*GIB:
            raise RuntimeError(f'Insufficient/unmeasurable free RAM: {available/GIB if available else 0:.2f} GiB')
        record['precheck']={'potential_rows':73_000_000,'expected_eligible_rows':expected,
             'free_disk_gib':round(free_before/GIB,2), 'available_ram_gib':round(available/GIB,2),
             'physical_ram_gib':round(total/GIB,2) if total else None,
             'second_full_copy_required':False}
        save()
        print('PRECHECK | '+json.dumps(record['precheck']),flush=True)
        # Real Parquet pilot: includes all data products and journal.
        stage='PARQUET_PILOT'
        pilot_cfg=GeneratorConfig(seed=cfg.seed,sku_count=100,store_count=8,days=90,
                                  store_batch=cfg.store_batch,format='parquet',
                                  movement_journal=True,supplier_count=cfg.supplier_count)
        pilot_cfg_path=root/'pilot_config.json'
        pilot_cfg_path.write_text(json.dumps(pilot_cfg.as_dict(),sort_keys=True,indent=2)+'\n',encoding='utf-8')
        pilot_out=root/'pilot'
        pilot_start=time.monotonic()
        run_task(stage,['-m','app.generation.cli','generate','--config',str(pilot_cfg_path),'--out',str(pilot_out)],
                 root/'pilot_generate.log',root,stop_disk_gib=stop_disk_gib)
        pilot_manifest=json.loads((pilot_out/'manifest.json').read_text())
        pilot_report=json.loads((pilot_out/'validation_summary.json').read_text())
        if pilot_report['status']!='PASS': raise RuntimeError('Pilot validator did not PASS')
        pilot_elapsed=time.monotonic()-pilot_start
        rows=pilot_manifest['entity_counts']['sales_history']
        bytes_on_disk=directory_bytes(pilot_out)
        if rows==0: raise RuntimeError('Pilot generated zero eligible rows')
        # Scaled estimate includes master/policy overhead and fixed partition costs.
        # Both conservative multiplier and free-space margin apply; no automatic bypass.
        estimate=bytes_on_disk/rows*expected*1.7
        free=shutil.disk_usage(root).free
        requirement=max(min_free_gib*GIB,estimate+8*GIB)
        record['pilot']={'eligible_rows':rows,'bytes':bytes_on_disk,
            'elapsed_seconds':round(pilot_elapsed,3),
            'eligible_rows_per_second':round(rows/pilot_elapsed,2) if pilot_elapsed else None,
            'projected_seconds_from_pilot':round(expected*pilot_elapsed/rows,2) if rows else None,
            'throughput_projection_warning':'pilot includes fixed setup and may not scale linearly',
            'estimated_full_gib_with_margin':round(estimate/GIB,2),
            'required_free_gib':round(requirement/GIB,2),
            'actual_free_gib':round(free/GIB,2)}
        if free < requirement:
            raise RuntimeError(f'Pilot resource gate blocked: {free/GIB:.2f} GiB available, {requirement/GIB:.2f} GiB required')
        record['stages']['pilot']='PASS';save()
        print('PILOT | '+json.dumps(record['pilot']),flush=True)
        stage='FULL_GENERATION_VALIDATION'
        full=root/'full_run'
        run_task(stage,['-m','app.generation.cli','generate','--profile','full','--out',str(full)],
                 root/'full_generate_validate.log',root,stop_disk_gib=stop_disk_gib)
        manifest=json.loads((full/'manifest.json').read_text())
        report=json.loads((full/'validation_summary.json').read_text())
        if report['status']!='PASS': raise RuntimeError('Full validation not PASS')
        if manifest['potential_sku_store_days']!=73_000_000 or manifest['entity_counts']['sku']!=2000 or manifest['entity_counts']['store']!=100 or manifest['entity_counts']['sales_history']!=expected:
            raise RuntimeError('Full required counts do not match')
        if manifest['entity_counts']['inventory_snapshot']!=expected+manifest['summary'].get('retired_inventory_snapshot_days',0) or manifest['entity_counts']['ground_truth_labels']!=expected:
            raise RuntimeError('Sales, inventory and truth cardinalities differ')
        if set(manifest['negative_scenarios']) != {'NEG-01','NEG-02','NEG-03','NEG-04','NEG-05'}:
            raise RuntimeError('Mandatory negative scenarios missing')
        overlay = report.get('negative_overlay_validation')
        if not overlay or overlay.get('status') != 'PASS':
            raise RuntimeError('Full dataset is missing validated schema-shaped issue overlays')
        record['stages']['full_generation_validation']='PASS';save()
        print('FULL_COUNTS | '+json.dumps(manifest['entity_counts']),flush=True)
        stage='FULL_INDEPENDENT_REPRODUCIBILITY'
        # Re-generates every logical row and checksum but retains only hashes.
        comparison=compare_streaming(full,progress=lambda p: print('REPRO_PROGRESS | '+json.dumps(p),flush=True))
        (root/'full_reproducibility.json').write_text(json.dumps(comparison,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        if comparison['status']!='PASS':
            raise RuntimeError('Independent full-scale reproducibility did not PASS: '+str(comparison['issues'][:3]))
        record['stages']['full_independent_reproducibility']='PASS';save()
        stage='FINAL'
        record['status']='PASS';record['result']={
            'counts':manifest['entity_counts'],'file_count':len(manifest['files']),
            'full_run_path':str(full),'checksum_comparison':str(root/'full_reproducibility.json'),
            'parquet_bytes':directory_bytes(full),
            'limitations':['local PostgreSQL fact import not tested',
                           'full data are lifecycle-eligible rows rather than Cartesian sales',
                           'Full-scale forecasting and replenishment are separate downstream acceptance gates']}
        save()
        print('OVERALL | FULL_SCALE_PASS',flush=True)
    except BaseException as exc:
        record['status']='FAIL';record['failed_stage']=stage
        record['error']=str(exc)
        record['traceback']=traceback.format_exc()[-6000:]
        save()
        print(f'OVERALL | FULL_SCALE_FAIL | stage={stage} | {exc}',file=sys.stderr,flush=True)
        raise
    return record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--min-free-gib',type=float,default=18)
    parser.add_argument('--min-available-ram-gib',type=float,default=2)
    args=parser.parse_args()
    if args.min_free_gib < 12 or args.min_available_ram_gib < 1.5:
        parser.error('Safety thresholds may not be set below 12 GiB disk / 1.5 GiB available RAM')
    run(args.out,min_free_gib=args.min_free_gib,min_ram_available_gib=args.min_available_ram_gib)

if __name__=='__main__': main()
