"""Optional out-of-core DuckDB read-only queries over observed Parquet history.

No hidden ground-truth paths are accepted. Used for profiling/analytics only;
forecast models and dashboard APIs are outside P1-T02 scope.
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from app.generation.model_inputs import assert_model_input_allowed


def observed_sales_summary(root: Path, *, origin: date) -> dict:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError('Install requirements-phase2.txt for DuckDB analytics') from exc
    root=root.resolve()
    paths=sorted((root/'facts'/'sales_history').glob('*/*.parquet'))
    if not paths: raise ValueError('No observed Parquet sales files; this query requires Parquet profile')
    for p in paths: assert_model_input_allowed(p)
    connection=duckdb.connect(database=':memory:')
    try:
        connection.execute("SET memory_limit='1GB'")
        connection.execute('SET threads=2')
        # File paths are restricted to actual filesystem-discovered, public fact files.
        # Pass them as query parameters, not dynamic SQL interpolated from user input.
        row=connection.execute(
            'SELECT COUNT(*) AS observations, COALESCE(SUM(units_sold),0) AS units_sold '
            'FROM read_parquet(?) WHERE sales_date <= ?',
            [[str(p) for p in paths], origin.isoformat()],
        ).fetchone()
        return {'origin':origin.isoformat(),'observations':row[0], 'units_sold':row[1],
                'truth_accessed':False,'engine':'duckdb'}
    finally:
        connection.close()
