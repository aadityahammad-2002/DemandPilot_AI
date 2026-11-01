"""Phase 4 CLI: execute, inspect and independently reproduce immutable decisions."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from app.replenishment.policy import DecisionConfig
from app.replenishment.engine import run
from app.replenishment.verify import verify,compare


def main():
    p=argparse.ArgumentParser(description='Phase 4 replenishment (no PostgreSQL writes)')
    sub=p.add_subparsers(dest='command',required=True)
    for cmd in ('run','reproduce'):
        q=sub.add_parser(cmd)
        q.add_argument('--data',required=True,type=Path)
        q.add_argument('--forecast',required=True,type=Path)
        q.add_argument('--out',required=True,type=Path)
        q.add_argument('--max-pairs',type=int,default=48)
        q.add_argument('--open-orders',type=Path,help='Optional explicitly verified external open-order CSV')
        q.add_argument('--max-extension-days',type=int,default=56)
    q=sub.add_parser('verify');q.add_argument('--out',required=True,type=Path)
    args=p.parse_args()
    if args.command=='verify':result=verify(args.out)
    else:
        cfg=DecisionConfig(max_pairs=args.max_pairs,max_extension_days=args.max_extension_days)
        if args.command=='run':
            result=run(args.data,args.forecast,args.out,cfg,open_orders_file=args.open_orders)
            verify(args.out)
        else:
            a=args.out/'first';b=args.out/'second'
            run(args.data,args.forecast,a,cfg,open_orders_file=args.open_orders);run(args.data,args.forecast,b,cfg,open_orders_file=args.open_orders)
            result=compare(a,b)
            args.out.mkdir(parents=True,exist_ok=True)
            (args.out/'reproducibility.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,sort_keys=True))
    if result.get('status')!='PASS':raise SystemExit(2)

if __name__=='__main__': main()
