"""Custom user-defined metrics."""

from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database.models import CustomMetric, User
from database.queries import Repo
from handlers.common import require_active, require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import (
    back_kb,
    bool_kb,
    cancel_kb,
    choices_kb,
    custom_metrics_kb,
    metric_card_kb,
    metric_duration_kb,
    metric_number_kb,
    metric_time_kb,
    metric_types_kb,
    metric_units_kb,
    when_kb,
)
from services.entries import add_custom_value, end_metric_period, start_metric_period
from services.metric_types import (
    UNIT_BY_KEY,
    created_metric_text,
    format_clock,
    get_type,
    metric_card_text,
    parse_metric_number,
    types_prompt,
    value_error,
    value_prompt,
)
from services.users import can_write
from states.diary import CustomMetricSG
from utils.callbacks import NAV_METRICS
from utils.telegram import safe_edit
from utils.time import parse_hhmm, parse_iso, parse_minutes_ago, to_iso, user_now

router = Router(name="custom_metrics")

METRICS_EMPTY = (
    "📌 <b>Кастомные метрики</b>\n\n"
    "Свои записи, которых нет в меню: вода, страницы, ванная — что угодно.\n\n"
    "Создайте первую метрику. Потом значения добавляются в пару нажатий."
)
METRICS_LIST = (
    "📌 <b>Кастомные метрики</b>\n\n"
    "➕ — записать значение. ▶️ / ⏹ — начало и конец интервала. Название — открыть метрику."
)
NAME_PROMPT = "Как назвать метрику? Например: вода, страницы, пульс."
UNIT_PROMPT = (
    "В каких единицах считать?\n\n"
    "Это подпись к числу. Можно без единицы или написать свою."
)
CHOICES_PROMPT = (
    "Какие варианты будут на кнопках?\n\n"
    "Напишите через запятую, минимум два.\n"
    "Пример: низкая, средняя, высокая"
)
UNAVAILABLE = "Метрика недоступна"


def _root_text(metrics) -> str:
    return METRICS_LIST if metrics else METRICS_EMPTY


async def show_custom_metrics(
    target: CallbackQuery | Message,
    repo: Repo,
    user: User,
    state: FSMContext | None = None,
) -> None:
    if state:
        await state.clear()
    metrics = await repo.list_metrics(user.telegram_id)
    open_ids = {item.metric_id for item in await repo.list_open_metric_values(user.telegram_id)}
    text, markup = _root_text(metrics), custom_metrics_kb(metrics, can_write(user), open_ids=open_ids)
    if isinstance(target, CallbackQuery):
        await safe_edit(target.message, text, markup)
        return
    await target.answer(text, reply_markup=markup)


async def _show_card(
    target: CallbackQuery | Message,
    user: User,
    metric: CustomMetric,
    repo: Repo,
    *,
    text: str | None = None,
) -> None:
    from services.ui_prefs import MAX_PINS

    open_period = None
    if metric.data_type == "period":
        open_period = await repo.get_open_metric_value(user.telegram_id, metric.id)
    body = text or metric_card_text(metric, open_period=open_period, tz=user.timezone)
    pinned_n = sum(1 for item in await repo.list_metrics(user.telegram_id) if item.pinned)
    markup = metric_card_kb(
        metric.id,
        bool(metric.enabled),
        can_write(user),
        pinned=bool(metric.pinned),
        can_pin=bool(metric.pinned) or pinned_n < MAX_PINS,
        data_type=metric.data_type,
        has_open=open_period is not None,
    )
    if isinstance(target, CallbackQuery):
        await safe_edit(target.message, body, markup)
        return
    await target.answer(body, reply_markup=markup)


def _value_markup(data_type: str, unit: str | None, metric_id: int) -> InlineKeyboardMarkup:
    back = f"cm:o:{metric_id}"
    if data_type == "number":
        return metric_number_kb(unit, back)
    if data_type == "duration":
        return metric_duration_kb(back)
    if data_type == "time":
        return metric_time_kb(back)
    return cancel_kb(back)


async def _finish_create(
    target: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    name: str,
    data_type: str,
    unit: str | None,
    choices: list[str] | None,
    *,
    toast: str = "Создано",
) -> None:
    metric_id = await repo.add_metric(user.telegram_id, name, data_type, unit, choices)
    metric = await repo.get_metric(metric_id, user.telegram_id)
    await state.clear()
    if metric is None:
        if isinstance(target, CallbackQuery):
            await target.answer(toast)
        await show_custom_metrics(target, repo, user, state)
        return
    if isinstance(target, CallbackQuery):
        await target.answer(toast)
    await _show_card(target, user, metric, repo, text=created_metric_text(metric))


async def _ask_when(event: CallbackQuery | Message, state: FSMContext, payload: dict) -> None:
    metric_id = int(payload["metric_id"])
    await state.update_data(**payload, time_exit=f"cm:{metric_id}")
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, "Когда зафиксировать?", when_kb("cmt", metric_id=metric_id))
        return
    await event.answer("Когда зафиксировать?", reply_markup=when_kb("cmt", metric_id=metric_id))


async def _begin_period(
    cb: CallbackQuery,
    state: FSMContext,
    user: User,
    metric: CustomMetric,
    action: str,
) -> None:
    await state.update_data(
        metric_id=metric.id,
        metric_name=metric.name,
        period_action=action,
        period_start=None,
        tz=user.timezone,
        time_exit=f"cm:{metric.id}",
    )
    if action == "end":
        prefix = "cme"
        prompt = f"Когда закончили «{metric.name}»?"
    elif action == "complete":
        prefix = "cms"
        prompt = f"Когда начали «{metric.name}»? Потом отметите, когда закончили."
    else:
        prefix = "cms"
        prompt = f"Когда начали «{metric.name}»?"
    await cb.answer()
    await safe_edit(cb.message, prompt, when_kb(prefix, metric_id=metric.id))


async def finish_period_start(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when,
) -> None:
    data = await state.get_data()
    metric_id = int(data["metric_id"])
    if data.get("period_action") == "complete":
        await state.update_data(period_start=to_iso(when), period_action="complete_end")
        name = data.get("metric_name") or "метрика"
        prompt = f"Когда закончили «{name}»?"
        markup = when_kb("cme", metric_id=metric_id)
        if isinstance(event, CallbackQuery):
            await event.answer()
            await safe_edit(event.message, prompt, markup)
            return
        await event.answer(prompt, reply_markup=markup)
        return
    item_id, error = await start_metric_period(repo, user, metric_id, when)
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    await show_saved_entry(event, repo, user, "cm", item_id, state)


async def finish_period_end(
    event: CallbackQuery | Message,
    state: FSMContext,
    repo: Repo,
    user: User,
    when,
) -> None:
    data = await state.get_data()
    metric_id = int(data["metric_id"])
    start_raw = data.get("period_start") if data.get("period_action") == "complete_end" else None
    start_at = parse_iso(start_raw) if start_raw else None
    item_id, error = await end_metric_period(repo, user, metric_id, when, start_at=start_at)
    if error:
        if isinstance(event, CallbackQuery):
            await event.answer(error, show_alert=True)
        else:
            await event.answer(error)
        return
    kind = "cm" if start_at is not None else "cme"
    await show_saved_entry(event, repo, user, kind, item_id, state)


@router.callback_query(F.data.startswith("cm:st:"))
async def metric_period_start(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric = await repo.get_metric(int(cb.data.split(":")[2]), user.telegram_id)
    if metric is None or not metric.enabled or metric.data_type != "period":
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    if await repo.get_open_metric_value(user.telegram_id, metric.id):
        await cb.answer("Уже идёт — сначала закончите.", show_alert=True)
        return
    await _begin_period(cb, state, user, metric, "start")


@router.callback_query(F.data.startswith("cm:en:"))
async def metric_period_end(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric = await repo.get_metric(int(cb.data.split(":")[2]), user.telegram_id)
    if metric is None or not metric.enabled or metric.data_type != "period":
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    open_rec = await repo.get_open_metric_value(user.telegram_id, metric.id)
    await _begin_period(cb, state, user, metric, "end" if open_rec else "complete")


@router.callback_query(F.data == "cms:now")
async def metric_period_start_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if "metric_id" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await finish_period_start(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data == "cme:now")
async def metric_period_end_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if "metric_id" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await finish_period_end(cb, state, repo, user, user_now(user.timezone))


@router.callback_query(F.data == "cms:time")
async def metric_period_start_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if "metric_id" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await start_time_pick(cb, state, "cm_start", {**data, "tz": user.timezone, "time_exit": f"cm:{data['metric_id']}"})


@router.callback_query(F.data == "cme:time")
async def metric_period_end_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if "metric_id" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await start_time_pick(cb, state, "cm_end", {**data, "tz": user.timezone, "time_exit": f"cm:{data['metric_id']}"})


def _parse_value(data_type: str, raw: str, unit: str | None) -> dict:
    spec = get_type(data_type)
    if spec.key == "duration":
        minutes = parse_minutes_ago(raw)
        if minutes <= 0 or minutes > 24 * 60:
            raise ValueError("duration")
        return {"value_number": float(minutes)}
    if spec.key == "number":
        return {"value_number": parse_metric_number(raw, unit)}
    if spec.key == "time":
        hour, minute = parse_hhmm(raw)
        return {"value_text": format_clock(hour, minute)}
    if not raw:
        raise ValueError("empty")
    return {"value_text": raw}


@router.callback_query(F.data == NAV_METRICS)
async def metrics_root(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await cb.answer()
    await show_custom_metrics(cb, repo, user, state)


@router.callback_query(F.data == "cm:new")
@router.callback_query(F.data == "cm:own")
@router.callback_query(F.data.startswith("cm:tpl:"))
async def metric_new(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await state.set_state(CustomMetricSG.name)
    await cb.answer()
    await safe_edit(cb.message, NAME_PROMPT, back_kb(NAV_METRICS))


@router.message(CustomMetricSG.name)
async def metric_name(message: Message, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(message, db_user) is None:
        return
    name = (message.text or "").strip()
    if not name or len(name) > 40:
        await message.answer("Имя 1–40 символов.", reply_markup=back_kb(NAV_METRICS))
        return
    await state.update_data(metric_name=name)
    await state.set_state(CustomMetricSG.data_type)
    await message.answer(types_prompt(name), reply_markup=metric_types_kb())


@router.callback_query(F.data.startswith("cm:t:"), CustomMetricSG.data_type)
async def metric_type(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    key = cb.data.split(":")[2]
    try:
        spec = get_type(key)
    except KeyError:
        await cb.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(data_type=key)
    if spec.needs_unit:
        await state.set_state(CustomMetricSG.unit)
        await cb.answer()
        await safe_edit(cb.message, UNIT_PROMPT, metric_units_kb())
        return
    if spec.needs_choices:
        await state.set_state(CustomMetricSG.choices)
        await cb.answer()
        await safe_edit(cb.message, CHOICES_PROMPT, back_kb("cm:types"))
        return
    data = await state.get_data()
    await _finish_create(cb, state, repo, user, data["metric_name"], key, None, None)


@router.callback_query(F.data == "cm:types", CustomMetricSG.unit)
@router.callback_query(F.data == "cm:types", CustomMetricSG.choices)
async def metric_back_types(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    data = await state.get_data()
    await state.set_state(CustomMetricSG.data_type)
    await cb.answer()
    await safe_edit(cb.message, types_prompt(data.get("metric_name") or "метрика"), metric_types_kb())


@router.callback_query(F.data.startswith("cm:u:"), CustomMetricSG.unit)
async def metric_unit_pick(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    token = cb.data.split(":")[2]
    if token == "own":
        await cb.answer()
        await safe_edit(cb.message, "Напишите единицу, например чашки, подходы, км.", back_kb("cm:types"))
        return
    unit = None if token == "none" else UNIT_BY_KEY.get(token)
    if token != "none" and unit is None:
        await cb.answer("Неизвестная единица", show_alert=True)
        return
    data = await state.get_data()
    await _finish_create(cb, state, repo, user, data["metric_name"], data["data_type"], unit, None)


@router.message(CustomMetricSG.unit)
async def metric_unit(message: Message, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    unit = (message.text or "").strip()[:20] or None
    await _finish_create(message, state, repo, user, data["metric_name"], data["data_type"], unit, None)


@router.message(CustomMetricSG.choices)
async def metric_choices(message: Message, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    choices = [part.strip() for part in (message.text or "").split(",") if part.strip()]
    if len(choices) < 2:
        await message.answer("Нужно минимум два варианта, через запятую.", reply_markup=back_kb("cm:types"))
        return
    data = await state.get_data()
    await _finish_create(message, state, repo, user, data["metric_name"], data["data_type"], None, choices)


@router.callback_query(F.data.startswith("cm:o:"))
async def metric_open(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await state.clear()
    metric = await repo.get_metric(int(cb.data.split(":")[2]), user.telegram_id)
    if metric is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    await cb.answer()
    await _show_card(cb, user, metric, repo)


@router.callback_query(F.data.startswith("cm:tog:"))
async def metric_toggle(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric_id = int(cb.data.split(":")[2])
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    await repo.update_metric(metric_id, user.telegram_id, enabled=0 if metric.enabled else 1)
    metric = await repo.get_metric(metric_id, user.telegram_id)
    await cb.answer("Сохранено")
    if metric is None:
        return
    await _show_card(cb, user, metric, repo)


@router.callback_query(F.data.startswith("cm:pin:"))
async def metric_pin(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    from services.ui_prefs import MAX_PINS

    metric_id = int(cb.data.split(":")[2])
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    if metric.pinned:
        await repo.update_metric(metric_id, user.telegram_id, pinned=0)
    else:
        pinned_n = sum(1 for item in await repo.list_metrics(user.telegram_id) if item.pinned)
        if pinned_n >= MAX_PINS:
            await cb.answer("На главной уже 3 метрики", show_alert=True)
            return
        await repo.update_metric(metric_id, user.telegram_id, pinned=1)
    metric = await repo.get_metric(metric_id, user.telegram_id)
    await cb.answer("Сохранено")
    if metric is None:
        return
    await _show_card(cb, user, metric, repo)


@router.callback_query(F.data.startswith("cm:add:"))
async def metric_add(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric_id = int(cb.data.split(":")[2])
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None or not metric.enabled:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await state.update_data(
        metric_id=metric_id,
        metric_name=metric.name,
        data_type=metric.data_type,
        choices_json=metric.choices_json,
        unit=metric.unit,
    )
    spec = get_type(metric.data_type)
    if spec.key == "period":
        open_rec = await repo.get_open_metric_value(user.telegram_id, metric_id)
        await _begin_period(cb, state, user, metric, "end" if open_rec else "start")
        return
    prompt = value_prompt(metric.name, metric.data_type, metric.unit)
    if spec.key == "boolean":
        await cb.answer()
        await safe_edit(cb.message, prompt, bool_kb(f"cm:o:{metric_id}"))
        return
    if spec.key == "choice":
        choices = json.loads(metric.choices_json or "[]")
        await cb.answer()
        await safe_edit(cb.message, prompt, choices_kb(choices, f"cm:o:{metric_id}"))
        return
    await state.set_state(CustomMetricSG.value)
    await cb.answer()
    await safe_edit(cb.message, prompt, _value_markup(spec.key, metric.unit, metric_id))


async def _apply_picked_value(cb: CallbackQuery, state: FSMContext, extra: dict) -> None:
    data = await state.get_data()
    if "metric_id" not in data:
        await cb.answer(UNAVAILABLE, show_alert=True)
        return
    await _ask_when(cb, state, {**data, **extra})


@router.callback_query(F.data.startswith("cm:v:"))
async def metric_bool(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await _apply_picked_value(cb, state, {"value_bool": int(cb.data.split(":")[2])})


@router.callback_query(F.data.startswith("cm:ch:"))
async def metric_choice(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    data = await state.get_data()
    choices = json.loads(data.get("choices_json") or "[]")
    idx = int(cb.data.split(":")[2])
    if idx < 0 or idx >= len(choices):
        await cb.answer("Нет такого варианта", show_alert=True)
        return
    await _apply_picked_value(cb, state, {"value_text": choices[idx]})


@router.callback_query(F.data.startswith("cm:q:"), CustomMetricSG.value)
async def metric_number_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    try:
        value = float(cb.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await cb.answer("Некорректное значение", show_alert=True)
        return
    await _apply_picked_value(cb, state, {"value_number": value})


@router.callback_query(F.data.startswith("cm:d:"), CustomMetricSG.value)
async def metric_duration_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await _apply_picked_value(cb, state, {"value_number": float(cb.data.split(":")[2])})


@router.callback_query(F.data.startswith("cm:tm:"), CustomMetricSG.value)
async def metric_time_pick(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    digits = cb.data.split(":")[2]
    stamp = format_clock(int(digits[:2]), int(digits[2:]))
    await _apply_picked_value(cb, state, {"value_text": stamp})


@router.message(CustomMetricSG.value)
async def metric_value(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    raw = (message.text or "").strip()
    try:
        parsed = _parse_value(data["data_type"], raw, data.get("unit"))
    except (KeyError, ValueError):
        metric_id = int(data["metric_id"])
        await message.answer(
            value_error(data["data_type"], data.get("unit")),
            reply_markup=_value_markup(data["data_type"], data.get("unit"), metric_id),
        )
        return
    await _ask_when(message, state, {**data, **parsed})


@router.callback_query(F.data == "cmt:now")
async def metric_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    item_id, error = await add_custom_value(
        repo,
        user,
        int(data["metric_id"]),
        user_now(user.timezone),
        value_number=data.get("value_number"),
        value_text=data.get("value_text"),
        value_bool=data.get("value_bool"),
    )
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "cm", item_id, state)


@router.callback_query(F.data == "cmt:time")
async def metric_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(
        cb,
        state,
        "cm",
        {**data, "tz": user.timezone, "time_exit": f"cm:{data['metric_id']}"},
    )
