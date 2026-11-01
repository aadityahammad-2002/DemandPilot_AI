"""Read-only live PostgreSQL verification of the Phase 5 technical ledger.

Usage (from backend folder): python verify_backend_schema_readonly.py
Uses DATABASE_URL from the current terminal. Does not print credentials, modify
schema, insert rows, or run any full-scale generation.
"""
from __future__ import annotations

from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app.config import database_url

from sqlalchemy import String
from app.models import Base
from app.integration.types import IntegrationBase


def expected_lengths():
    """Model-declared minimum VARCHAR sizes across core + technical tables."""
    values={}
    for metadata in (Base.metadata,IntegrationBase.metadata):
        for table in metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type,String) and column.type.length is not None:
                    values[(table.name,column.name)]=column.type.length
    return values


def verify(engine):
    with engine.connect() as conn:
        if conn.dialect.name!='postgresql':
            raise RuntimeError('The schema audit requires PostgreSQL')
        conn.execute(text('SET TRANSACTION READ ONLY'))
        revision=conn.scalar(text('SELECT version_num FROM alembic_version'))
        columns=conn.execute(text('''
            SELECT table_name,column_name,character_maximum_length
            FROM information_schema.columns
            WHERE table_schema='public'
        ''')).all()
        observed={(table,col):size for table,col,size in columns}
        expected=expected_lengths()
        for key,size in sorted(expected.items()):
            actual=observed.get(key)
            print(f'{key[0]}.{key[1]}: {actual} (required >= {size})')
            if actual is None or actual<size:
                raise ValueError(f'column length mismatch at {key[0]}.{key[1]}')
        if revision!='0005':
            raise ValueError(f'expected Alembic 0005; found {revision}')
        print(f'READ-ONLY POSTGRESQL SCHEMA CHECK: PASS (revision 0005; {len(expected)} string columns)')


if __name__=='__main__':
    url=database_url()  # honors backend/.env and exported environment overrides
    parsed=make_url(url)
    if (parsed.drivername!='postgresql+psycopg' or parsed.database!='yottaflex_dual_v7'
            or parsed.host not in ('localhost','127.0.0.1')):
        raise SystemExit('Refusing schema check: expected local PostgreSQL yottaflex_dual_v7 only')
    engine=create_engine(url,pool_pre_ping=True)
    try:verify(engine)
    finally:engine.dispose()
