"""CLI entrypoints: train-evaluate, verify, reproduce. Existing data remains read-only."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from app.forecasting.config import ForecastConfig
from app.forecasting.engine import forecast
from app.forecasting.verify import verify_run, compare_runs


def main():
    parser=argparse.ArgumentParser(description='Phase 3 point-in-time demand forecasting (no DB writes)')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('train-evaluate','reproduce'):
        p=sub.add_parser(name)
        p.add_argument('--data',type=Path,required=True,help='Phase 2 generated dataset root')
        p.add_argument('--out',type=Path,required=True,help='New output folder (never overwritten)')
        p.add_argument('--max-pairs',type=int,default=48)
        p.add_argument('--promo-calendar',type=Path,default=None,
                       help='Optional public planned promotions with known_at timestamps')
        p.add_argument('--max-train-rows',type=int,default=40000)
        p.add_argument('--seed',type=int,default=20261030)
    p=sub.add_parser('oracle-evaluate')
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('verify')
    p.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='verify':
        result=verify_run(args.out)
    elif args.command=='oracle-evaluate':
        from app.forecasting.oracle import oracle_evaluate
        result=oracle_evaluate(args.data,args.out)
    else:
        cfg=ForecastConfig(seed=args.seed,max_pairs=args.max_pairs,max_train_rows=args.max_train_rows)
        if args.command=='train-evaluate':
            result=forecast(args.data,args.out,cfg,args.promo_calendar)
            verify_run(args.out)
        else:
            a,b=args.out/'run_a',args.out/'run_b'
            forecast(args.data,a,cfg,args.promo_calendar); verify_run(a)
            forecast(args.data,b,cfg,args.promo_calendar); verify_run(b)
            result=compare_runs(a,b)
            args.out.mkdir(parents=True,exist_ok=True)
            (args.out/'reproducibility.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,sort_keys=True))
    if result.get('status')=='FAIL': raise SystemExit(2)

if __name__=='__main__': main()
