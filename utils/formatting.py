"""User-facing text helpers."""

from __future__ import annotations

from datetime import timedelta

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
    "choice": "выбор из вариантов",
    "time": "время",
    "duration": "длительность",
}

ENTRY_TITLES = {
    "cigarette": "🚬 Сигарета",
    "fooling": "🤡 Валять дурака",
    "snus": "🟢 Снюс",
    "sleep": "😴 Сон",
    "mood": "🙂 Настроение",
    "wellbeing": "❤️ Самочувствие",
    "caffeine": "☕ Кофеин",
    "alcohol": "🍺 Алкоголь",
    "activity": "🏃 Активность",
    "note": "📝 Заметка",
    "custom": "📌 Показатель",
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


def paid_days(balance: float, daily_price: float) -> str:
    if daily_price <= 0:
        return "безлимит"
    if balance <= 0:
        return "0"
    days = int(balance // daily_price)
    return str(days)


def balance_runway(balance: float, daily_price: float) -> str:
    days = paid_days(balance, daily_price)
    if days == "безлимит":
        return "безлимит"
    count = int(days)
    if count <= 0:
        return "уже не хватает"
    return f"хватит ещё ~{count} {_days_ru(count)}"


def timedelta_human(delta: timedelta) -> str:
    total = int(delta.total_seconds() // 60)
    return duration_human(total)


def truncate(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
