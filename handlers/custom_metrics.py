"""Custom user-defined metrics."""

from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database.models import User
from database.queries import Repo
from handlers.common import require_active, require_writable, show_main, start_time_pick
from keyboards.main import (
    bool_kb,
    cancel_kb,
    choices_kb,
    custom_metrics_kb,
    metric_card_kb,
    metric_types_kb,
    now_or_time,
)
from services.entries import add_custom_value
from services.metric_types import METRIC_TYPES, get_type
from services.users import can_write
from states.diary import CustomMetricSG
from utils.callbacks import NAV_METRICS
from utils.telegram import safe_edit
from utils.time import parse_hhmm, user_now

router = Router(name="custom_metrics")


@router.callback_query(F.data == NAV_METRICS)
async def metrics_root(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    metrics = await repo.list_metrics(user.telegram_id)
    await cb.answer()
    await safe_edit(
        cb.message,
        "📌 Ваши показатели",
        custom_metrics_kb(metrics, can_write(user)),
    )


@router.callback_query(F.data == "cm:new")
async def metric_new(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.set_state(CustomMetricSG.name)
    await cb.answer()
    await safe_edit(cb.message, "Название показателя:", cancel_kb())


@router.message(CustomMetricSG.name)
async def metric_name(message: Message, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(message, db_user) is None:
        return
    name = (message.text or "").strip()
    if not name or len(name) > 40:
        await message.answer("Имя 1–40 символов.", reply_markup=cancel_kb())
        return
    await state.update_data(metric_name=name)
    await state.set_state(CustomMetricSG.data_type)
    await message.answer("Тип данных:", reply_markup=metric_types_kb())


@router.callback_query(F.data.startswith("cm:t:"), CustomMetricSG.data_type)
async def metric_type(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
    config: Config,
    is_owner: bool,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    key = cb.data.split(":")[2]
    spec = get_type(key)
    await state.update_data(data_type=key)
    if spec.needs_unit:
        await state.set_state(CustomMetricSG.unit)
        await cb.answer()
        await safe_edit(cb.message, "Единица измерения (шт, мл, стр…):", cancel_kb())
        return
    if spec.needs_choices:
        await state.set_state(CustomMetricSG.choices)
        await cb.answer()
        await safe_edit(cb.message, "Варианты через запятую:", cancel_kb())
        return
    data = await state.get_data()
    await _create(repo, user, data["metric_name"], key, None, None)
    await cb.answer("Создано")
    await show_main(cb, user, config, is_owner, state)


@router.message(CustomMetricSG.unit)
async def metric_unit(message: Message, state: FSMContext, repo: Repo, db_user: User | None, config: Config, is_owner: bool) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    unit = (message.text or "").strip()[:20]
    await _create(repo, user, data["metric_name"], data["data_type"], unit, None)
    await state.clear()
    await message.answer("Показатель создан.")
    await show_main(message, user, config, is_owner, state)


@router.message(CustomMetricSG.choices)
async def metric_choices(message: Message, state: FSMContext, repo: Repo, db_user: User | None, config: Config, is_owner: bool) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    choices = [p.strip() for p in (message.text or "").split(",") if p.strip()]
    if len(choices) < 2:
        await message.answer("Нужно минимум два варианта.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await _create(repo, user, data["metric_name"], data["data_type"], None, choices)
    await state.clear()
    await message.answer("Показатель создан.")
    await show_main(message, user, config, is_owner, state)


async def _create(repo: Repo, user: User, name: str, data_type: str, unit, choices) -> int:
    return await repo.add_metric(user.telegram_id, name, data_type, unit, choices)


@router.callback_query(F.data.startswith("cm:o:"))
async def metric_open(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    metric_id = int(cb.data.split(":")[2])
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None:
        await cb.answer("Не найдено", show_alert=True)
        return
    spec = METRIC_TYPES[metric.data_type]
    text = (
        f"📌 <b>{metric.name}</b>\n"
        f"Тип: {spec.label}\n"
        f"Ед.: {metric.unit or '—'}\n"
        f"Статус: {'вкл' if metric.enabled else 'выкл'}"
    )
    await cb.answer()
    await safe_edit(cb.message, text, metric_card_kb(metric.id, bool(metric.enabled), can_write(user)))


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
    await safe_edit(
        cb.message,
        f"📌 {metric.name}",
        metric_card_kb(metric.id, bool(metric.enabled), True),
    )


@router.callback_query(F.data.startswith("cm:add:"))
async def metric_add(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric_id = int(cb.data.split(":")[2])
    metric = await repo.get_metric(metric_id, user.telegram_id)
    if metric is None or not metric.enabled:
        await cb.answer("Показатель недоступен", show_alert=True)
        return
    await state.update_data(metric_id=metric_id, data_type=metric.data_type, choices_json=metric.choices_json)
    spec = get_type(metric.data_type)
    if spec.key == "boolean":
        await cb.answer()
        await safe_edit(cb.message, f"{metric.name}: да или нет?", bool_kb())
        return
    if spec.key == "choice":
        choices = json.loads(metric.choices_json or "[]")
        await cb.answer()
        await safe_edit(cb.message, f"{metric.name}: выберите значение", choices_kb(choices))
        return
    await state.set_state(CustomMetricSG.value)
    await cb.answer()
    await safe_edit(cb.message, f"Введите значение для «{metric.name}»", cancel_kb())


@router.callback_query(F.data.startswith("cm:v:"))
async def metric_bool(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    value = int(cb.data.split(":")[2])
    await state.update_data(value_bool=value)
    await start_time_pick(cb, state, "cm", {**(await state.get_data()), "tz": user.timezone, "value_bool": value})


@router.callback_query(F.data.startswith("cm:ch:"))
async def metric_choice(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    idx = int(cb.data.split(":")[2])
    data = await state.get_data()
    choices = json.loads(data.get("choices_json") or "[]")
    text = choices[idx]
    await start_time_pick(cb, state, "cm", {**data, "tz": user.timezone, "value_text": text})


@router.message(CustomMetricSG.value)
async def metric_value(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    spec = get_type(data["data_type"])
    raw = (message.text or "").strip()
    payload = dict(data)
    try:
        if spec.key in {"number", "duration"}:
            payload["value_number"] = float(raw.replace(",", "."))
        elif spec.key == "time":
            parse_hhmm(raw)
            payload["value_text"] = raw
        else:
            payload["value_text"] = raw
    except ValueError:
        await message.answer("Некорректное значение", reply_markup=cancel_kb())
        return
    await state.update_data(**payload)
    await message.answer("Когда зафиксировать?", reply_markup=now_or_time("cmt"))


@router.callback_query(F.data == "cmt:now")
async def metric_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    config: Config,
    db_user: User | None,
    is_owner: bool,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    _, error = await add_custom_value(
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
    await cb.answer("Сохранено")
    await show_main(cb, user, config, is_owner, state)


@router.callback_query(F.data == "cmt:time")
async def metric_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    await start_time_pick(cb, state, "cm", {**data, "tz": user.timezone})
