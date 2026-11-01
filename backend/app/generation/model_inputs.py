"""Point-in-time data boundary: hidden truth must never be included in model features."""
from pathlib import Path


def assert_model_input_allowed(path: Path, origin_date: str | None = None) -> None:
    normalized={p.lower() for p in path.parts}
    if normalized.intersection({'restricted_ground_truth','negative_fixtures'}):
        raise PermissionError('hidden labels and injected negative fixtures are not model inputs')
    if path.name.startswith('manifest') or path.name.startswith('validation'):
        raise PermissionError('run metadata is not a modeling input')
    # The later forecast feature builder must enforce row event_time <= origin_date.
    # Path-only validation is deliberately NOT claimed to prevent temporal leakage.
