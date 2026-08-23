"""Extensible custom metric types."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from utils.quantity import is_volume_unit, parse_drink_amount, quantity_in_unit

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
        hint="Количество: вода, шаги, вес, страницы",
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
}


@dataclass(frozen=True, slots=True)
class MetricTemplate:
    key: str
    button: str
    name: str
    data_type: str
    unit: str | None = None
    choices: tuple[str, ...] | None = None


METRIC_TEMPLATES: tuple[MetricTemplate, ...] = (
    MetricTemplate("water", "💧 Вода · мл", "Вода", "number", "мл"),
    MetricTemplate("steps", "🚶 Шаги", "Шаги", "number", "шаги"),
    MetricTemplate("weight", "⚖️ Вес · кг", "Вес", "number", "кг"),
    MetricTemplate("pages", "📖 Страницы", "Страницы", "number", "стр"),
    MetricTemplate("meds", "💊 Лекарства · да/нет", "Лекарства", "boolean"),
    MetricTemplate("energy", "⚡️ Энергия · выбор", "Энергия", "choice", None, ("низкая", "средняя", "высокая")),
    MetricTemplate("focus", "⏱ Фокус · минуты", "Фокус", "duration"),
)

TEMPLATE_BY_KEY = {item.key: item for item in METRIC_TEMPLATES}

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


def get_template(key: str) -> MetricTemplate | None:
    return TEMPLATE_BY_KEY.get(key)


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


def metric_card_text(metric) -> str:
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
    lines.append(spec.hint)
    lines.append(f"Пример: {spec.example}")
    return "\n".join(lines)


def created_metric_text(metric) -> str:
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


def format_clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"
