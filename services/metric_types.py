"""Extensible custom metric types."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from utils.formatting import duration_human
from utils.quantity import is_volume_unit, parse_drink_amount, quantity_in_unit
from utils.time import format_time, parse_iso

_LEADING_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class MetricType:
    key: str
    label: str
    emoji: str
    hint: str
    example: str
    needs_unit: bool = False
    needs_choices: bool = False
    numeric: bool = False

    @property
    def button_label(self) -> str:
        return f"{self.emoji} {self.label}"


METRIC_TYPES: dict[str, MetricType] = {
    "number": MetricType(
        "number",
        "Число",
        "🔢",
        hint="Количество: вода, страницы, повторения",
        example="250, 0.5л, 8000",
        needs_unit=True,
        numeric=True,
    ),
    "text": MetricType(
        "text",
        "Текст",
        "📝",
        hint="Свободная короткая запись",
        example="прогулка в парке",
    ),
    "boolean": MetricType(
        "boolean",
        "Да / нет",
        "✅",
        hint="Случилось или нет — одной кнопкой",
        example="принял лекарства, была тренировка",
    ),
    "choice": MetricType(
        "choice",
        "Выбор",
        "📋",
        hint="Свои варианты на кнопках",
        example="низкая / средняя / высокая",
        needs_choices=True,
    ),
    "time": MetricType(
        "time",
        "Время суток",
        "🕐",
        hint="Часы и минуты, не длительность",
        example="07:30",
    ),
    "duration": MetricType(
        "duration",
        "Длительность",
        "⏱",
        hint="Сколько заняло: минуты и часы",
        example="20 мин, 1ч 15м",
        numeric=True,
    ),
    "period": MetricType(
        "period",
        "Интервал",
        "▶️",
        hint="Начал и закончил: ванная, работа, поездка",
        example="вошёл → вышел",
        numeric=True,
    ),
}


UNIT_PRESETS: tuple[tuple[str, str], ...] = (
    ("ml", "мл"),
    ("l", "л"),
    ("pcs", "шт"),
    ("kg", "кг"),
    ("km", "км"),
    ("pg", "стр"),
    ("steps", "шаги"),
    ("min", "мин"),
    ("rub", "₽"),
    ("pct", "%"),
)

UNIT_BY_KEY = {key: label for key, label in UNIT_PRESETS}


def get_type(key: str) -> MetricType:
    if key not in METRIC_TYPES:
        raise KeyError(key)
    return METRIC_TYPES[key]


def types_prompt(name: str) -> str:
    lines = [
        f"Как будете записывать «{name}»?",
        "",
        "От типа зависит, что вы нажмёте или напишете каждый раз:",
        "",
    ]
    for spec in METRIC_TYPES.values():
        lines.append(f"{spec.emoji} <b>{spec.label}</b> — {spec.hint}")
        lines.append(f"Пример: {spec.example}")
        lines.append("")
    lines.append("Выберите тип:")
    return "\n".join(lines)


def is_period_open(rec) -> bool:
    return getattr(rec, "data_type", None) == "period" and rec.value_bool == 1


def format_period_value(rec, tz: str | None = None) -> str:
    start = format_time(parse_iso(rec.occurred_at), tz) if tz else rec.occurred_at
    if is_period_open(rec) or not rec.value_text:
        return f"идёт с {start}"
    end = format_time(parse_iso(rec.value_text), tz) if tz else rec.value_text
    minutes = int(rec.value_number) if rec.value_number is not None else None
    span = f" ({duration_human(minutes)})" if minutes is not None else ""
    return f"{start} — {end}{span}"


def format_metric_value(rec, tz: str | None = None) -> str:
    if getattr(rec, "data_type", None) == "period":
        return format_period_value(rec, tz)
    if rec.value_number is not None:
        text = f"{rec.value_number:g}"
        if rec.unit:
            text += f" {rec.unit}"
        return text
    if rec.value_bool is not None:
        return "да" if rec.value_bool else "нет"
    return rec.value_text or ""


def metric_card_text(metric, *, open_period=None, tz: str | None = None) -> str:
    spec = METRIC_TYPES[metric.data_type]
    status = "включена" if metric.enabled else "выключена"
    lines = [f"📌 <b>{metric.name}</b>", "", f"Тип: {spec.emoji} {spec.label}"]
    if metric.unit:
        lines.append(f"Единица: {metric.unit}")
    if spec.needs_choices:
        choices = json.loads(metric.choices_json or "[]")
        if choices:
            lines.append("Варианты: " + ", ".join(choices))
    lines.append(f"Статус: {status}")
    lines.append("")
    if spec.key == "period":
        if open_period is not None:
            lines.append(f"Сейчас: {format_period_value(open_period, tz)}")
            lines.append("Нажмите «Закончил», когда выйдете.")
        else:
            lines.append(spec.hint)
            lines.append("«Начал» — вход. «Закончил» — выход; если вход не отмечали, спросим когда заходили.")
        return "\n".join(lines)
    lines.append(spec.hint)
    lines.append(f"Пример: {spec.example}")
    return "\n".join(lines)


def created_metric_text(metric) -> str:
    if getattr(metric, "data_type", None) == "period":
        return (
            f"Метрика «{metric.name}» создана. Можно сразу нажать «Начал» или «Закончил».\n\n"
            + metric_card_text(metric)
        )
    return f"Метрика «{metric.name}» создана. Можно сразу записать значение.\n\n" + metric_card_text(metric)


def value_prompt(name: str, data_type: str, unit: str | None = None) -> str:
    spec = get_type(data_type)
    if spec.key == "duration":
        return (
            f"Сколько длилось «{name}»?\n"
            "Нажмите кнопку или напишите: 20, 1 час, 1ч 15м."
        )
    if spec.key == "number":
        unit_bit = f" ({unit})" if unit else ""
        extra = ""
        if is_volume_unit(unit):
            extra = "\nМожно 250, 250 мл или 0.5л."
        elif unit:
            extra = f"\nМожно число, например 3 {unit}."
        return f"«{name}»{unit_bit} — сколько?{extra}"
    if spec.key == "time":
        return (
            f"Время для «{name}» — ЧЧ:ММ, например 07:30.\n"
            "Это само значение, не момент записи."
        )
    if spec.key == "text":
        return f"Короткий текст для «{name}»:"
    if spec.key == "boolean":
        return f"«{name}»: да или нет?"
    if spec.key == "choice":
        return f"«{name}»: выберите значение"
    return f"Значение для «{name}»"


def value_error(data_type: str, unit: str | None = None) -> str:
    spec = get_type(data_type)
    if spec.key == "duration":
        return "Введите длительность, например 20, 90 мин, 1 час или 1ч 15м."
    if spec.key == "number" and is_volume_unit(unit):
        return "Введите объём, например 250, 250 мл или 0.5л."
    if spec.key == "number":
        return "Введите число, например 3 или 1,5."
    if spec.key == "time":
        return "Введите время ЧЧ:ММ, например 07:30."
    return "Не получилось понять значение. Попробуйте ещё раз."


def parse_metric_number(raw: str, unit: str | None) -> float:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty")
    if is_volume_unit(unit):
        return quantity_in_unit(parse_drink_amount(text), unit or "")
    compact = re.sub(r"(?<=\d)\s+(?=\d)", "", text).replace(",", ".")
    match = _LEADING_NUMBER.match(compact)
    if not match:
        raise ValueError("number")
    return float(match.group(0))


MAX_STEPS = 200_000
MIN_WEIGHT_KG = 1.0
MAX_WEIGHT_KG = 500.0


def parse_steps(raw: str) -> int:
    value = parse_metric_number(raw, "шаги")
    if abs(value - round(value)) > 1e-9:
        raise ValueError("steps")
    count = int(round(value))
    if count < 0 or count > MAX_STEPS:
        raise ValueError("steps")
    return count


def parse_weight_kg(raw: str) -> float:
    value = parse_metric_number(raw, "кг")
    if value < MIN_WEIGHT_KG or value > MAX_WEIGHT_KG:
        raise ValueError("weight")
    return round(value, 2)


def format_clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"
