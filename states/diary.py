"""FSM states for multi-step flows."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class RegisterSG(StatesGroup):
    consent = State()
    timezone = State()
    timezone_custom = State()


class TimePickSG(StatesGroup):
    date = State()
    hour = State()
    minute = State()
    manual = State()
    ago_pick = State()
    ago_minutes = State()
    when_text = State()


class SleepSG(StatesGroup):
    quality = State()
    when = State()


class AmountSG(StatesGroup):
    value = State()


class ActivitySG(StatesGroup):
    duration = State()
    comment = State()


class StepsSG(StatesGroup):
    pick_date = State()
    value = State()


class WeightSG(StatesGroup):
    value = State()


class CustomMetricSG(StatesGroup):
    name = State()
    data_type = State()
    unit = State()
    choices = State()
    value = State()


class MarkerSG(StatesGroup):
    name = State()
    comment = State()
    edit_name = State()
    edit_comment = State()
    join = State()
    pick_end = State()


class HistorySG(StatesGroup):
    custom_date = State()
    range_end = State()


class StatsSG(StatesGroup):
    custom_date = State()
    range_end = State()


class SettingsSG(StatesGroup):
    timezone_custom = State()
    sleep_time = State()
    confirm_delete = State()


class AdminSG(StatesGroup):
    search = State()
    amount = State()
    comment = State()
    price = State()
    sql = State()
    purge_confirm = State()
    restore_file = State()
    restore_confirm = State()
    restore_disk = State()
    broadcast = State()


class PaidSG(StatesGroup):
    amount = State()
