from __future__ import annotations

import pytest

from utils.quantity import (
    format_quantity,
    format_volume_ml,
    milliliters_of,
    parse_drink_amount,
    quantity_in_unit,
)


def test_parse_alcohol_milliliters_and_liters():
    assert parse_drink_amount("500").milliliters == 500
    assert parse_drink_amount("500 мл").milliliters == 500
    assert parse_drink_amount("500мл").milliliters == 500
    assert parse_drink_amount("0.5л").milliliters == 500
    assert parse_drink_amount("0,5 л").milliliters == 500
    assert parse_drink_amount("0.33").milliliters == 330
    assert parse_drink_amount("1").milliliters == 1000
    assert parse_drink_amount("330 ml").milliliters == 330
    assert parse_drink_amount("пол-литра").milliliters == 500
    assert parse_drink_amount("1 порция").unit == "шт"
    assert parse_drink_amount("2 порции").amount == 2


def test_parse_caffeine_small_integer_is_cup():
    qty = parse_drink_amount("1", small_integer="count")
    assert qty.unit == "шт"
    assert qty.milliliters is None
    assert parse_drink_amount("250", small_integer="count").milliliters == 250
    assert parse_drink_amount("0.5л", small_integer="count").milliliters == 500


def test_parse_drink_amount_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_drink_amount("")
    with pytest.raises(ValueError):
        parse_drink_amount("много")
    with pytest.raises(ValueError):
        parse_drink_amount("20 л")
    with pytest.raises(ValueError):
        parse_drink_amount("-1")


def test_format_volume_and_quantity():
    assert format_volume_ml(330) == "330 мл"
    assert format_volume_ml(500) == "0,5 л"
    assert format_volume_ml(1500) == "1,5 л"
    assert format_quantity(500, "мл") == "0,5 л"
    assert format_quantity(1, "шт") == "1 шт"
    assert format_quantity(250, "шт") == "250 мл"
    assert milliliters_of(0.5, "л") == 500
    assert milliliters_of(1, "шт") is None
    assert milliliters_of(250, "шт") == 250


def test_quantity_converts_into_metric_unit():
    qty = parse_drink_amount("0.5л")
    assert quantity_in_unit(qty, "мл") == 500
    assert quantity_in_unit(qty, "л") == 0.5
