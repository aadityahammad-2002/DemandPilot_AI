"""P1-T02 CLI: run with python -m app.generation.cli --help."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from app.generation.config import GeneratorConfig
from app.generation.generator import generate
from app.generation.sql_seed import export_sql
from app.generation.verify import validate, compare_runs


def main() -> None:
    p=argparse.ArgumentParser(description='Deterministic synthetic retail generation (no DB mutations)')
    sub=p.add_subparsers(dest='cmd',required=True)
    for task in ('generate','reproduce'):
        c=sub.add_parser(task)
        c.add_argument('--out',type=Path,required=True)
        c.add_argument('--config',type=Path)
        c.add_argument('--profile',choices=('smoke','full'),default='smoke')
        c.add_argument('--format',choices=('csv','parquet'))
        c.add_argument('--seed',type=int,default=20261030)
    v=sub.add_parser('validate')
    v.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.cmd=='validate':
        print(json.dumps(validate(args.out),sort_keys=True))
        return
    if args.config:
        cfg=GeneratorConfig.from_json(args.config)
    elif args.profile=='smoke':
        cfg=GeneratorConfig(seed=args.seed,sku_count=12,store_count=3,days=35,
                            store_batch=1,format='csv')
    else:
        cfg=GeneratorConfig(seed=args.seed)
    if args.format:
        d=cfg.as_dict();d['format']=args.format;cfg=GeneratorConfig(**d)
    cfg.validate()
    if args.cmd=='generate':
        result=generate(cfg,args.out)
        sql=export_sql(args.out)
        report=validate(args.out)
        print(json.dumps({'status':report['status'],'out':str(args.out),
                          'counts':result['entity_counts'],'sql_seed':sql['path']},sort_keys=True))
    else:
        a=args.out/'run_a';b=args.out/'run_b'
        generate(cfg,a);export_sql(a);validate(a)
        generate(cfg,b);export_sql(b);validate(b)
        comparison=compare_runs(a,b)
        (args.out/'reproducibility_report.json').write_text(json.dumps(comparison,indent=2,sort_keys=True)+'\n')
        print(json.dumps(comparison,sort_keys=True))
        if comparison['status']!='PASS':
            raise SystemExit(2)

if __name__=='__main__': main()
