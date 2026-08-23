from __future__ import annotations

from types import SimpleNamespace

from services.metric_types import (
    created_metric_text,
    format_clock,
    get_template,
    metric_card_text,
    parse_metric_number,
    types_prompt,
    value_prompt,
)


def test_parse_metric_number_accepts_spaces_and_units():
    assert parse_metric_number("8 000 шагов", "шаги") == 8000
    assert parse_metric_number("1,5 кг", "кг") == 1.5
    assert parse_metric_number("250 мл", "мл") == 250
    assert parse_metric_number("0.5л", "мл") == 500


def test_types_prompt_explains_each_choice():
    text = types_prompt("Вода")
    assert "Как будете записывать «Вода»?" in text
    assert "Число" in text
    assert "Количество" in text
    assert "Да / нет" in text
    assert "Время суток" in text


def test_value_prompt_mentions_unit_and_examples():
    assert "мл" in value_prompt("Вода", "number", "мл")
    assert "0.5л" in value_prompt("Вода", "number", "мл")
    assert "1ч 15м" in value_prompt("Фокус", "duration")
    assert "не момент записи" in value_prompt("Подъём", "time")


def test_water_template_and_card_text():
    template = get_template("water")
    assert template is not None
    assert template.data_type == "number"
    assert template.unit == "мл"
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
