"""Extensible custom metric types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricType:
    key: str
    label: str
    needs_unit: bool = False
    needs_choices: bool = False
    numeric: bool = False


METRIC_TYPES: dict[str, MetricType] = {
    "number": MetricType("number", "Число", needs_unit=True, numeric=True),
    "text": MetricType("text", "Текст"),
    "boolean": MetricType("boolean", "Да / нет"),
    "choice": MetricType("choice", "Выбор из вариантов", needs_choices=True),
    "time": MetricType("time", "Время"),
    "duration": MetricType("duration", "Длительность", numeric=True),
}


def get_type(key: str) -> MetricType:
    if key not in METRIC_TYPES:
        raise KeyError(key)
    return METRIC_TYPES[key]
