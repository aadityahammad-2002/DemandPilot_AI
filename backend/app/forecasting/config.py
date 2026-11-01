"""Immutable forecasting configuration and temporal split contracts."""
from dataclasses import asdict, dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ForecastConfig:
    seed: int = 20261030
    max_pairs: int = 48              # explicit development scope, not full-scale coverage
    max_train_rows: int = 40000     # bounded reservoir for CPU/RAM
    max_origins_per_split: int = 12
    origin_stride: int = 7
    horizons: tuple[int, ...] = (1, 7, 14, 28)
    min_history: int = 7
    model_iterations: int = 75

    def validate(self) -> None:
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ValueError('seed must be an integer')
        if not 1 <= self.max_pairs <= 200000:
            raise ValueError('max_pairs must be in 1..200000; use bounded batches for large runs')
        if not 100 <= self.max_train_rows <= 250000:
            raise ValueError('max_train_rows must be in 100..250000')
        if not 1 <= self.max_origins_per_split <= 100:
            raise ValueError('invalid max_origins_per_split')
        if not 1 <= self.origin_stride <= 60:
            raise ValueError('invalid origin_stride')
        if not self.horizons or any(not isinstance(x, int) or x < 1 or x > 90 for x in self.horizons):
            raise ValueError('invalid horizons')
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError('duplicate horizons')
        if not 3 <= self.min_history <= 60:
            raise ValueError('invalid min_history')
        if not 10 <= self.model_iterations <= 300:
            raise ValueError('invalid model_iterations')

    def as_dict(self) -> dict:
        d = asdict(self)
        d['horizons'] = list(self.horizons)
        return d


def chronological_split(start: date, days: int) -> dict[str, tuple[date, date]]:
    """Half-open periods: 365 -> 245/60/60; smoke -> disjoint 60/20/20%."""
    if days < 21:
        raise ValueError('at least 21 days required for chronological evaluation')
    train = 245 if days == 365 else max(12, int(days * .60))
    validation = 60 if days == 365 else max(4, (days - train) // 2)
    test = days - train - validation
    if test < 4:
        raise ValueError('too few test days')
    s1 = start + timedelta(days=train)
    s2 = s1 + timedelta(days=validation)
    s3 = start + timedelta(days=days)
    return {'train': (start, s1), 'validation': (s1, s2), 'test': (s2, s3)}
