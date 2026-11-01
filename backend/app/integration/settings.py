"""Local-first secure settings. Credentials never included in API responses."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import os
from dotenv import load_dotenv

@lru_cache
def settings():
    backend=Path(__file__).resolve().parents[2]
    # Explicit exported environment variables always override backend/.env.
    load_dotenv(backend/'.env',override=False)
    project=backend.parent
    return {
        'database_url':os.getenv('DATABASE_URL',''),
        'cors_origins':[s.strip() for s in os.getenv('YOTTA_CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(',') if s.strip()],
        'data_root':Path(os.getenv('YOTTA_DATA_ROOT',str(project/'examples'/'smoke_run'))).resolve(),
        'artifacts_root':Path(os.getenv('YOTTA_ARTIFACTS_ROOT',str(project/'evidence'/'phase5_runs'))).resolve(),
        'max_pairs':int(os.getenv('YOTTA_MAX_PAIRS','36')),
        'allow_demo_import':os.getenv('YOTTA_ALLOW_IMPORT','false').lower()=='true',
    }

def validate_settings(config):
    if not config['database_url'].startswith('postgresql+psycopg://'):
        raise ValueError('A PostgreSQL DATABASE_URL is required')
    if not 1 <= config['max_pairs'] <= 2000:
        raise ValueError('YOTTA_MAX_PAIRS must be 1..2000; development runs only')
    if '*' in config['cors_origins']:
        raise ValueError('Wildcard CORS not allowed')
