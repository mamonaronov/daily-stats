"""FSM states for multi-step flows."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class RegisterSG(StatesGroup):
    timezone = State()
    timezone_custom = State()


class TimePickSG(StatesGroup):
    date = State()
    hour = State()
    minute = State()
    manual = State()


class SleepSG(StatesGroup):
    quality = State()
    when = State()


class WellbeingSG(StatesGroup):
    comment = State()


class AmountSG(StatesGroup):
    value = State()


class ActivitySG(StatesGroup):
    duration = State()
    comment = State()


class NoteSG(StatesGroup):
    text = State()


class CustomMetricSG(StatesGroup):
    name = State()
    data_type = State()
    unit = State()
    choices = State()
    value = State()


class HistorySG(StatesGroup):
    custom_date = State()
    range_end = State()


class StatsSG(StatesGroup):
    custom_date = State()
    range_end = State()


class DayReviewSG(StatesGroup):
    mood = State()
    wellbeing = State()
    comment = State()


class SettingsSG(StatesGroup):
    timezone_custom = State()
    sleep_time = State()
    confirm_delete = State()


class AdminSG(StatesGroup):
    search = State()
    amount = State()
    comment = State()
    price = State()
