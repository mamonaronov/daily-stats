"""Parse drink volumes and related user quantities."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER_UNIT = re.compile(r"^([+-]?\d+(?:\.\d+)?)(.*)$")

_VOLUME_ML = {
    "мл",
    "ml",
    "миллилитр",
    "миллилитра",
    "миллилитров",
}
_VOLUME_L = {
    "л",
    "l",
    "литр",
    "литра",
    "литров",
}
_COUNT = {
    "шт",
    "штука",
    "штуки",
    "штук",
    "порция",
    "порции",
    "порций",
    "чашка",
    "чашки",
    "чашек",
    "чашку",
    "кружка",
    "кружки",
    "кружек",
    "кружку",
    "бокал",
    "бокала",
    "бокалов",
    "рюмка",
    "рюмки",
    "рюмок",
    "банка",
    "банки",
    "банок",
    "банку",
    "cup",
    "cups",
    "shot",
    "shots",
    "glass",
    "glasses",
}
_HALF_LITER = {"поллитра", "поллитр"}

MAX_ML = 10_000
MAX_COUNT = 50


@dataclass(frozen=True, slots=True)
class Quantity:
    amount: float
    unit: str
    milliliters: float | None

    @classmethod
    def from_ml(cls, milliliters: float) -> Quantity:
        return cls(amount=milliliters, unit="мл", milliliters=milliliters)

    @classmethod
    def from_count(cls, amount: float) -> Quantity:
        return cls(amount=amount, unit="шт", milliliters=None)

    def display(self) -> str:
        if self.milliliters is not None:
            return format_volume_ml(self.milliliters)
        return f"{format_number(self.amount)} {self.unit}"


def format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def format_volume_ml(milliliters: float) -> str:
    if milliliters <= 0:
        return "0 мл"
    if milliliters >= 500:
        return f"{format_number(milliliters / 1000)} л"
    if abs(milliliters - round(milliliters)) < 1e-6:
        return f"{int(round(milliliters))} мл"
    return f"{format_number(milliliters)} мл"


def format_quantity(amount: float | None, unit: str | None) -> str:
    if amount is None:
        return ""
    milliliters = milliliters_of(amount, unit)
    if milliliters is not None:
        return format_volume_ml(milliliters)
    label = _canonical_unit(unit or "") or (unit or "шт").strip() or "шт"
    if label in _COUNT:
        label = "шт"
    return f"{format_number(amount)} {label}"


def is_volume_unit(unit: str | None) -> bool:
    key = _canonical_unit(unit or "")
    return key in {"мл", "л"}


def milliliters_of(amount: float | None, unit: str | None) -> float | None:
    """Canonical milliliters for a stored drink record, including legacy шт values."""
    if amount is None or amount <= 0:
        return None
    key = _canonical_unit(unit or "")
    if key == "мл":
        return amount
    if key == "л":
        return amount * 1000
    if key == "шт" or not key:
        if amount >= 10:
            return amount
        return None
    return None


def quantity_in_unit(qty: Quantity, unit: str) -> float:
    key = _canonical_unit(unit) or unit.strip().lower()
    if qty.milliliters is None:
        if key == "шт":
            return qty.amount
        raise ValueError("volume")
    if key == "л":
        return qty.milliliters / 1000
    return qty.milliliters


def parse_drink_amount(text: str, *, small_integer: str = "liters") -> Quantity:
    """Parse a drink amount.

    ``small_integer`` is ``liters`` (alcohol) or ``count`` (caffeine cups).
    Bare values of 10 and above are milliliters. Values below 10 without a unit
    are litres, unless ``small_integer='count'`` and the number is a whole cup.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip().lower().replace(",", "."))
    if not raw:
        raise ValueError("empty")
    alias = re.sub(r"[\s\-]+", "", raw)
    if alias in _HALF_LITER:
        return Quantity.from_ml(500)

    match = _NUMBER_UNIT.fullmatch(raw)
    if not match:
        raise ValueError("amount")
    value = float(match.group(1))
    unit_raw = (match.group(2) or "").strip(" .")
    if value <= 0:
        raise ValueError("amount")

    key = _canonical_unit(unit_raw) if unit_raw else None
    if unit_raw and key is None:
        raise ValueError("unit")

    if key == "л":
        return Quantity.from_ml(_check_ml(value * 1000))
    if key == "мл":
        return Quantity.from_ml(_check_ml(value))
    if key == "шт":
        if value > MAX_COUNT:
            raise ValueError("amount")
        return Quantity.from_count(value)

    if value < 10:
        if small_integer == "count" and abs(value - round(value)) < 1e-9:
            if value > MAX_COUNT:
                raise ValueError("amount")
            return Quantity.from_count(value)
        return Quantity.from_ml(_check_ml(value * 1000))
    return Quantity.from_ml(_check_ml(value))


def _check_ml(milliliters: float) -> float:
    if milliliters < 1 or milliliters > MAX_ML:
        raise ValueError("range")
    return milliliters


def _canonical_unit(raw: str) -> str | None:
    token = (raw or "").strip().lower().strip(" .")
    if not token:
        return None
    token = token.split()[0]
    if token in _VOLUME_ML:
        return "мл"
    if token in _VOLUME_L:
        return "л"
    if token in _COUNT:
        return "шт"
    return None
