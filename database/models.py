"""Dataclasses mirroring SQLite rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class User:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    registered_at: str
    timezone: str
    status: str
    last_activity_at: str | None
    balance: float
    daily_price: float
    paid_until_date: str | None
    last_charge_date: str | None
    deleted_at: str | None
    bot_blocked_at: str | None
    created_at: str
    updated_at: str
    default_sleep_time: str = "23:00"
    stats_prefs_json: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.deleted_at is None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None or self.status == "deleted"

    @property
    def is_banned(self) -> bool:
        return self.status == "banned"

    @property
    def is_bot_blocked(self) -> bool:
        return self.status == "bot_blocked" or self.bot_blocked_at is not None

    @property
    def display_name(self) -> str:
        return self.first_name or self.username or str(self.telegram_id)


@dataclass(slots=True)
class BalanceOp:
    id: int
    telegram_id: int
    amount: float
    operation_type: str
    balance_before: float
    balance_after: float
    created_at: str
    comment: str | None
    performed_by: int | None
    idempotency_key: str | None


@dataclass(slots=True)
class Cigarette:
    id: int
    telegram_id: int
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class Fooling:
    id: int
    telegram_id: int
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class SleepRecord:
    id: int
    telegram_id: int
    bedtime: str | None
    wake_time: str | None
    duration_minutes: int | None
    quality: int | None
    created_at: str
    updated_at: str
    phone_in_bed_at: str | None = None
    phone_away_at: str | None = None
    sleep_onset_at: str | None = None
    out_of_bed_at: str | None = None

    def phase(self) -> str:
        if self.wake_time is None:
            if self.phone_in_bed_at and not self.phone_away_at:
                return "with_phone"
            if self.phone_away_at or self.bedtime:
                return "no_phone"
            return "idle"
        if self.out_of_bed_at is None:
            return "awake"
        if self.sleep_onset_at is None:
            return "need_onset"
        return "idle"


@dataclass(slots=True)
class SnusPack:
    id: int
    telegram_id: int
    bought_at: str | None
    finished_at: str | None
    duration_minutes: int | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class MoodRecord:
    id: int
    telegram_id: int
    score: int
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class WellbeingRecord:
    id: int
    telegram_id: int
    score: int
    comment: str | None
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class CaffeineRecord:
    id: int
    telegram_id: int
    drink_type: str
    amount: float | None
    unit: str | None
    extra_json: str | None
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class AlcoholRecord:
    id: int
    telegram_id: int
    drink_type: str
    amount: float | None
    unit: str | None
    extra_json: str | None
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class ActivityRecord:
    id: int
    telegram_id: int
    activity_type: str
    duration_minutes: int | None
    comment: str | None
    extra_json: str | None
    occurred_at: str
    created_at: str


@dataclass(slots=True)
class Note:
    id: int
    telegram_id: int
    body: str
    occurred_at: str
    created_at: str
    updated_at: str | None


@dataclass(slots=True)
class CustomMetric:
    id: int
    telegram_id: int
    name: str
    data_type: str
    unit: str | None
    choices_json: str | None
    enabled: int
    created_at: str


@dataclass(slots=True)
class CustomValue:
    id: int
    telegram_id: int
    metric_id: int
    value_number: float | None
    value_text: str | None
    value_bool: int | None
    occurred_at: str
    created_at: str
    metric_name: str | None = None
    data_type: str | None = None
    unit: str | None = None


@dataclass(slots=True)
class VpnLatencySample:
    id: int
    measured_at: str
    ok: int
    latency_ms: int | None
    node_name: str | None
    subscription: str | None
    error: str | None
    host_uptime_s: float | None = None


@dataclass(slots=True)
class TimelineItem:
    kind: str
    id: int
    occurred_at: datetime
    title: str
    detail: str
    extra: dict
