from __future__ import annotations

from types import SimpleNamespace

from services.metric_types import (
    created_metric_text,
    format_clock,
    format_period_value,
    metric_card_text,
    parse_metric_number,
    parse_steps,
    parse_weight_kg,
    types_prompt,
    value_prompt,
)


def test_parse_metric_number_accepts_spaces_and_units():
    assert parse_metric_number("8 000 шагов", "шаги") == 8000
    assert parse_metric_number("1,5 кг", "кг") == 1.5
    assert parse_metric_number("250 мл", "мл") == 250
    assert parse_metric_number("0.5л", "мл") == 500


def test_parse_steps_and_weight():
    assert parse_steps("8 000") == 8000
    assert parse_steps("12000 шагов") == 12000
    try:
        parse_steps("12.5")
        raise AssertionError("expected error")
    except ValueError:
        pass
    assert parse_weight_kg("72,4") == 72.4
    assert parse_weight_kg("70 кг") == 70
    try:
        parse_weight_kg("0.2")
        raise AssertionError("expected error")
    except ValueError:
        pass


def test_types_prompt_explains_each_choice():
    text = types_prompt("Вода")
    assert "Как будете записывать «Вода»?" in text
    assert "Число" in text
    assert "Количество" in text
    assert "Да / нет" in text
    assert "Время суток" in text
    assert "Интервал" in text


def test_value_prompt_mentions_unit_and_examples():
    assert "мл" in value_prompt("Вода", "number", "мл")
    assert "0.5л" in value_prompt("Вода", "number", "мл")
    assert "1ч 15м" in value_prompt("Фокус", "duration")
    assert "не момент записи" in value_prompt("Подъём", "time")


def test_number_metric_card_text():
    metric = SimpleNamespace(
        name="Вода",
        data_type="number",
        unit="мл",
        choices_json=None,
        enabled=1,
    )
    card = metric_card_text(metric)
    assert "Вода" in card
    assert "мл" in card
    assert "включена" in card
    created = created_metric_text(metric)
    assert "создана" in created
    assert "записать значение" in created


def test_format_clock():
    assert format_clock(7, 5) == "07:05"


def test_period_card_explains_start_and_end():
    metric = SimpleNamespace(
        name="Ванная",
        data_type="period",
        unit=None,
        choices_json=None,
        enabled=1,
    )
    card = metric_card_text(metric)
    assert "Интервал" in card
    assert "Начал" in card
    assert "Закончил" in card
    created = created_metric_text(metric)
    assert "Начал" in created
    open_rec = SimpleNamespace(
        data_type="period",
        occurred_at="2026-08-26T15:00:00+00:00",
        value_text=None,
        value_number=None,
        value_bool=1,
    )
    running = metric_card_text(metric, open_period=open_rec, tz="UTC")
    assert "идёт с 15:00" in running
    closed = SimpleNamespace(
        data_type="period",
        occurred_at="2026-08-26T15:00:00+00:00",
        value_text="2026-08-26T15:20:00+00:00",
        value_number=20,
        value_bool=0,
    )
    assert format_period_value(closed, "UTC") == "15:00 — 15:20 (20 мин)"
