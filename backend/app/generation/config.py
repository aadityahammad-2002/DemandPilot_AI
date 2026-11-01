"""Configuration for the reproducible, bounded-memory synthetic data generator."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
import json


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 20261030
    sku_count: int = 2000
    store_count: int = 100
    days: int = 365
    start_date: str = "2026-01-01"
    store_batch: int = 2
    format: str = "parquet"  # parquet (pyarrow required) or csv
    supplier_count: int = 80
    movement_journal: bool = True

    def validate(self) -> None:
        if not (1 <= self.sku_count <= 2000 and 1 <= self.store_count <= 100):
            raise ValueError("SKU count must be 1..2000, store count 1..100")
        if not (1 <= self.days <= 365):
            raise ValueError("days must be 1..365")
        if not (1 <= self.store_batch <= self.store_count):
            raise ValueError("store_batch must be between 1 and store_count")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ValueError("seed must be an integer")
        if not isinstance(self.movement_journal, bool):
            raise ValueError("movement_journal must be boolean")
        if self.format not in ("parquet", "csv"):
            raise ValueError("format must be parquet or csv")
        if not (1 <= self.supplier_count <= 999999):
            raise ValueError("supplier_count out of range")
        d = date.fromisoformat(self.start_date)
        if d.year < 2000 or (d + timedelta(days=self.days - 1)).year > 9999:
            raise ValueError("start_date outside supported date range")
        # All master SKU identifiers and policy IDs must fit the six-digit contract.
        if self.sku_count * self.store_count > 999999:
            raise ValueError("policy ID space exhausted")

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, path: Path) -> "GeneratorConfig":
        data = json.loads(path.read_text(encoding="utf-8"))
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown configuration keys: {sorted(unknown)}")
        obj = cls(**data)
        obj.validate()
        return obj
