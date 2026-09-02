"""Main-menu visibility and related UI flags stored as JSON on the user."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from database.models import User
from database.queries import Repo

TRACKABLE_TYPES = (
    "cigarettes",
    "fooling",
    "snus",
    "sleep",
    "caffeine",
    "alcohol",
    "activity",
    "steps",
    "weight",
    "custom",
    "markers",
)

TRACKABLE_LABELS = {
    "cigarettes": "🚬 Сигареты",
    "fooling": "🤌 Валять дурака",
    "snus": "🟢 Снюс",
    "sleep": "😴 Сон",
    "caffeine": "☕ Кофеин",
    "alcohol": "🍺 Алкоголь",
    "activity": "🏃 Активность",
    "steps": "🚶 Шаги",
    "weight": "⚖️ Вес",
    "custom": "📌 Кастом",
    "markers": "🔖 Метки",
}

# Old "hide buttons" list — used only to migrate prefs that still store `hidden`.
_LEGACY_HIDEABLE = frozenset(
    {
        "snus",
        "fooling",
        "caffeine",
        "alcohol",
        "activity",
        "custom",
        "markers",
        "steps",
        "weight",
    }
)

MAX_PINS = 3


@dataclass(slots=True)
class UiPrefs:
    tracked: set[str] = field(default_factory=set)
    onboarded: bool = False
    low_balance_notice_on: str | None = None
    owner_digest_on: str | None = None

    def is_tracked(self, key: str) -> bool:
        return key in self.tracked

    def to_json(self) -> str:
        return json.dumps(
            {
                "tracked": sorted(self.tracked),
                "onboarded": self.onboarded,
                "low_balance_notice_on": self.low_balance_notice_on,
                "owner_digest_on": self.owner_digest_on,
            },
            ensure_ascii=False,
        )


def _legacy_tracked(hidden: set[str]) -> set[str]:
    return set(TRACKABLE_TYPES) - hidden


def parse_ui_prefs(raw: str | None) -> UiPrefs:
    if not raw:
        return UiPrefs(tracked=_legacy_tracked(set()))
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return UiPrefs(tracked=_legacy_tracked(set()))
    if "tracked" in data:
        tracked = {item for item in data.get("tracked") or [] if item in TRACKABLE_TYPES}
    else:
        hidden = {item for item in data.get("hidden") or [] if item in _LEGACY_HIDEABLE}
        tracked = _legacy_tracked(hidden)
    return UiPrefs(
        tracked=tracked,
        onboarded=bool(data.get("onboarded")),
        low_balance_notice_on=data.get("low_balance_notice_on"),
        owner_digest_on=data.get("owner_digest_on"),
    )


def prefs_of(user: User) -> UiPrefs:
    return parse_ui_prefs(getattr(user, "ui_prefs_json", None))


async def save_prefs(repo: Repo, user: User, prefs: UiPrefs) -> User:
    await repo.update_settings(user.telegram_id, ui_prefs_json=prefs.to_json())
    updated = await repo.get_user(user.telegram_id)
    assert updated is not None
    return updated


def toggle_tracked(prefs: UiPrefs, key: str) -> UiPrefs:
    if key not in TRACKABLE_TYPES:
        return prefs
    tracked = set(prefs.tracked)
    if key in tracked:
        tracked.remove(key)
    else:
        tracked.add(key)
    prefs.tracked = tracked
    return prefs
