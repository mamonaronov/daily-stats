"""User-facing text helpers."""

from __future__ import annotations

from datetime import date, timedelta

SCORE_LABELS = {
    1: "ужасно",
    2: "плохо",
    3: "нормально",
    4: "хорошо",
    5: "отлично",
}

SCORE_EMOJI = {
    1: "😢",
    2: "😞",
    3: "😐",
    4: "🙂",
    5: "🤩",
}

CAFFEINE_TYPES = {
    "coffee": "кофе",
    "energy": "энергетик",
    "tea": "чай",
    "other": "другое",
}

ALCOHOL_TYPES = {
    "beer": "пиво",
    "wine": "вино",
    "spirits": "крепкий алкоголь",
    "cocktail": "коктейль",
    "other": "другое",
}

ACTIVITY_TYPES = {
    "walk": "ходьба",
    "run": "бег",
    "workout": "тренировка",
    "bike": "велосипед",
    "other": "другое",
}

METRIC_TYPE_LABELS = {
    "number": "число",
    "text": "текст",
    "boolean": "да/нет",
    "choice": "выбор",
    "time": "время суток",
    "duration": "длительность",
}

ENTRY_TITLES = {
    "cigarette": "🚬 Сигарета",
    "fooling": "🤌 Валять дурака",
    "snus": "🟢 Снюс",
    "sleep": "😴 Сон",
    "caffeine": "☕ Кофеин",
    "alcohol": "🍺 Алкоголь",
    "activity": "🏃 Активность",
    "custom": "📌 Кастом",
}

BALANCE_ENDED = (
    "Баланс закончился. Новые записи временно недоступны.\n\n"
    "Ваша история и статистика по-прежнему доступны.\n\n"
    "Для продолжения использования пополните баланс."
)

DELETED_ACCOUNT = (
    "Аккаунт удалён. Данные сохранены для аудита.\n\n"
    "Нажмите /start, чтобы восстановить доступ к дневнику."
)

BANNED_ACCOUNT = "Доступ к боту ограничен. Если это ошибка, напишите владельцу сервиса."


def money(amount: float) -> str:
    if abs(amount - round(amount)) < 1e-9:
        return f"{int(round(amount))} ₽"
    return f"{amount:.2f} ₽".replace(".", ",")


def duration_human(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    total = max(0, minutes)
    days, rem = divmod(total, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if mins or not parts:
        parts.append(f"{mins} мин")
    return " ".join(parts)


def seconds_human(seconds: int | float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(round(seconds)))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if secs and not days:
        parts.append(f"{secs} с")
    if not parts:
        return "0 с"
    return " ".join(parts)


def bytes_human(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} КБ".replace(".", ",")
    return f"{size / (1024 * 1024):.1f} МБ".replace(".", ",")


def score_text(score: int) -> str:
    return f"{SCORE_EMOJI.get(score, '')} {SCORE_LABELS.get(score, str(score))}".strip()


def _days_ru(n: int) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def _parse_paid_until(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def extra_paid_days(balance: float, daily_price: float) -> int | None:
    """Full extra days the leftover balance can cover. None = unlimited."""
    if daily_price <= 0:
        return None
    if balance <= 0:
        return 0
    return int(balance // daily_price)


def paid_days(balance: float, daily_price: float) -> str:
    extra = extra_paid_days(balance, daily_price)
    if extra is None:
        return "безлимит"
    return str(extra)


def coverage(
    balance: float,
    daily_price: float,
    today: date,
    paid_until_date: str | date | None = None,
) -> tuple[int | None, date | None]:
    """Inclusive remaining access days and last covered date.

    Days is None when the daily price is free. Date is None when there is
    no remaining coverage (including today).
    """
    extra = extra_paid_days(balance, daily_price)
    if extra is None:
        return None, None
    already_paid = _parse_paid_until(paid_until_date)
    if already_paid is not None and already_paid >= today:
        until = already_paid + timedelta(days=extra)
        return (until - today).days + 1, until
    if extra <= 0:
        return 0, None
    until = today + timedelta(days=extra - 1)
    return extra, until


def _coverage_of(user, today: date | None = None) -> tuple[int | None, date | None]:
    from utils.time import user_today

    day = today or user_today(user.timezone)
    return coverage(user.balance, user.daily_price, day, user.paid_until_date)


def balance_runway(user, *, today: date | None = None) -> str:
    from utils.time import format_date_long

    days, until = _coverage_of(user, today)
    if days is None:
        return "безлимит"
    if days <= 0 or until is None:
        return "уже не хватает"
    return f"осталось {days} {_days_ru(days)}, хватит до {format_date_long(until)}"


def balance_coverage_block(user, *, today: date | None = None) -> str:
    from utils.time import format_date_long

    days, until = _coverage_of(user, today)
    if days is None:
        return "Безлимит"
    if days <= 0 or until is None:
        return "Уже не хватает"
    return (
        f"Осталось: {days} {_days_ru(days)}\n"
        f"Хватит до: {format_date_long(until)}"
    )


def timedelta_human(delta: timedelta) -> str:
    total = int(delta.total_seconds() // 60)
    return duration_human(total)


def truncate(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
