"""Main-menu visibility and related UI flags stored as JSON on the user."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from database.models import User
from database.queries import Repo

HIDEABLE_TYPES = (
    "snus",
    "fooling",
    "caffeine",
    "alcohol",
    "activity",
    "custom",
    "markers",
    "steps",
    "weight",
)

HIDEABLE_LABELS = {
    "snus": "🟢 Снюс",
    "fooling": "🤌 Валять дурака",
    "caffeine": "☕ Кофеин",
    "alcohol": "🍺 Алкоголь",
    "activity": "🏃 Активность",
    "custom": "📌 Кастом",
    "markers": "🔖 Метки",
    "steps": "🚶 Шаги",
    "weight": "⚖️ Вес",
}

MAX_PINS = 3


@dataclass(slots=True)
class UiPrefs:
    hidden: set[str] = field(default_factory=set)
    onboarded: bool = False
    low_balance_notice_on: str | None = None
    owner_digest_on: str | None = None

    def is_hidden(self, key: str) -> bool:
        return key in self.hidden

    def to_json(self) -> str:
        return json.dumps(
            {
                "hidden": sorted(self.hidden),
                "onboarded": self.onboarded,
                "low_balance_notice_on": self.low_balance_notice_on,
                "owner_digest_on": self.owner_digest_on,
            },
            ensure_ascii=False,
        )


def parse_ui_prefs(raw: str | None) -> UiPrefs:
    if not raw:
        return UiPrefs()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return UiPrefs()
    hidden = {item for item in data.get("hidden") or [] if item in HIDEABLE_TYPES}
    return UiPrefs(
        hidden=hidden,
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


def toggle_hidden(prefs: UiPrefs, key: str) -> UiPrefs:
    if key not in HIDEABLE_TYPES:
        return prefs
    hidden = set(prefs.hidden)
    if key in hidden:
        hidden.remove(key)
    else:
        hidden.add(key)
    prefs.hidden = hidden
    return prefs
