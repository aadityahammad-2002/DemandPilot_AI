"""Offline PostgreSQL seed SQL for master/policy/supplier observations only.

Not executed by the generator. Fact data (millions of rows) remains Parquet/CSV.
"""
from __future__ import annotations
import csv
from pathlib import Path
from app.generation.generator import MASTER_COLUMNS


def quote(v: str) -> str:
    if v is None or v == '': return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def export_sql(root: Path) -> dict:
    root=root.resolve()
    path=root/'sql'/'master_seed.sql'
    path.parent.mkdir(parents=True,exist_ok=True)
    count=0
    with path.open('w', encoding='utf-8',newline='\n') as sql:
        sql.write('-- YottaFlex synthetic master seed; candidate review required before executing.\n')
        sql.write('-- Requires PostgreSQL schema at migration 0002 and an EMPTY/compatible target.\n')
        sql.write('-- NEVER run automatically against an existing evaluation database.\n')
        sql.write('BEGIN;\n')
        for table in ['sku','store','supplier','supplier_sku','reorder_policy','lead_time_observation']:
            cols=MASTER_COLUMNS[table]
            with (root/'master'/f'{table}.csv').open(newline='',encoding='utf-8') as handle:
                for r in csv.DictReader(handle):
                    sql.write('INSERT INTO '+table+' ('+', '.join(cols)+') VALUES ('+
                              ', '.join(quote(r[c]) for c in cols)+');\n')
                    count+=1
        sql.write('COMMIT;\n')
    return {'path':path.relative_to(root).as_posix(),'rows':count,'bytes':path.stat().st_size}
