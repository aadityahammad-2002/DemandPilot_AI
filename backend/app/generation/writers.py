"""Streaming partition writers with deterministic logical-content hashes."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from typing import Iterable


def canonical(row: dict) -> bytes:
    return (json.dumps({k: '' if v is None else str(v) for k, v in row.items()}, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


class PartitionWriter:
    def __init__(self, path: Path, fields: Iterable[str], fmt: str, *, buffer_size: int = 2048):
        self.path = path
        self.fields = tuple(fields)
        self.format = fmt
        self.count = 0
        self.logical_hash = hashlib.sha256()
        self.buffer_size = buffer_size
        self.buffer = []
        path.parent.mkdir(parents=True, exist_ok=True)
        self.output = None
        self.writer = None
        self.parquet_writer = None
        if fmt == 'csv':
            self.output = path.open('w', newline='', encoding='utf-8')
            self.writer = csv.DictWriter(self.output, fieldnames=self.fields, extrasaction='raise', lineterminator='\n')
            self.writer.writeheader()
        elif fmt == 'parquet':
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise RuntimeError('Parquet requires pyarrow: python -m pip install -r requirements-phase2.txt') from exc
            self.pa, self.pq = pa, pq
        else:
            raise ValueError(fmt)

    def add(self, row: dict) -> None:
        if set(row) != set(self.fields):
            raise ValueError(f"wrong fields for {self.path.name}: {set(row) ^ set(self.fields)}")
        normalized = {k: row[k] for k in self.fields}
        self.logical_hash.update(canonical(normalized))
        self.count += 1
        if self.format == 'csv':
            self.writer.writerow(normalized)
        else:
            self.buffer.append(normalized)
            if len(self.buffer) >= self.buffer_size:
                self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        table = self.pa.Table.from_pylist(self.buffer)
        if self.parquet_writer is None:
            self.parquet_writer = self.pq.ParquetWriter(
                self.path, table.schema, compression='zstd', use_dictionary=True)
        self.parquet_writer.write_table(table)
        self.buffer.clear()

    def close(self) -> dict:
        if self.format == 'parquet':
            self.flush()
            if self.parquet_writer:
                self.parquet_writer.close()
            else:
                raise ValueError('no rows in partition; do not create empty Parquet partition')
        else:
            self.output.close()
        with self.path.open('rb') as fh:
            file_hash = hashlib.file_digest(fh, 'sha256').hexdigest()
        return {'path': self.path.as_posix(), 'rows': self.count,
                'logical_sha256': self.logical_hash.hexdigest(),
                'bytes': self.path.stat().st_size,
                'file_sha256': file_hash}


def write_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> dict:
    out = PartitionWriter(path, fields, 'csv')
    try:
        for row in rows:
            out.add(row)
    except BaseException:
        if out.output:
            out.output.close()
        raise
    return out.close()
