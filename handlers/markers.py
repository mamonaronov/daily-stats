"""Named event markers and periods for charts/history."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import EventPeriod, User
from database.queries import Repo
from handlers.common import require_active, require_writable, start_time_pick
from handlers.history import show_saved_entry
from keyboards.main import (
    confirm_unlink_kb,
    marker_card_kb,
    marker_name_kb,
    marker_pick_kb,
    markers_root_kb,
    period_card_kb,
    period_pick_kb,
    skip_comment_kb,
    when_kb,
)
from services import markers as marker_svc
from services.users import can_write
from states.diary import MarkerSG
from utils.callbacks import NAV_MARKERS
from utils.formatting import duration_human
from utils.telegram import safe_edit
from utils.time import format_dt, parse_iso, user_now

router = Router(name="markers")

MARKERS_INTRO = (
    "🔖 <b>Временные метки</b>\n\n"
    "Метка — событие с названием и временем, например экзамен. "
    "На графиках они видны как ориентиры.\n\n"
    "Две метки можно связать в период: создать начало, затем конец, "
    "или объединить уже существующие. Период можно убрать — метки останутся."
)
NAME_PROMPT = "Название метки? Например: экзамен, отпуск, сессия."
COMMENT_PROMPT = "Комментарий? Можно пропустить."
WHEN_PROMPT = "Когда поставить метку?"


def _marker_payload(data: dict, tz: str) -> dict:
    extra = {
        "tz": tz,
        "marker_name": data.get("marker_name"),
        "marker_comment": data.get("marker_comment"),
        "marker_mode": data.get("marker_mode") or "plain",
        "time_exit": "when:mkt",
    }
    if data.get("close_period_id") is not None:
        extra["close_period_id"] = data["close_period_id"]
    return extra


async def show_markers_root(
    event: CallbackQuery | Message,
    repo: Repo,
    user: User,
    state: FSMContext | None = None,
) -> None:
    if state:
        await state.clear()
    recent = await repo.list_recent_markers(user.telegram_id, 15)
    open_periods = await repo.list_open_periods(user.telegram_id)
    shown = {period.start_marker_id for period in open_periods}
    recent = [item for item in recent if item.id not in shown]
    text = MARKERS_INTRO
    if open_periods:
        text += f"\n\nОткрытых периодов: {len(open_periods)}"
    elif not recent:
        text += "\n\nПока нет меток — создайте первую."
    markup = markers_root_kb(recent, open_periods, can_write(user), user.timezone)
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


async def _show_marker_card(
    event: CallbackQuery | Message,
    repo: Repo,
    user: User,
    marker_id: int,
    state: FSMContext | None = None,
    *,
    heading: str | None = None,
    toast: str = "",
    undo: bool = False,
) -> None:
    if state:
        await state.clear()
    text = await marker_card_text(repo, user, marker_id, heading=heading)
    rec = await repo.get_marker(marker_id, user.telegram_id)
    period_id = rec.period_id if rec else None
    markup = marker_card_kb(marker_id, can_write(user), period_id=period_id, undo=undo)
    if isinstance(event, CallbackQuery):
        if toast:
            await event.answer(toast)
        else:
            await event.answer()
        await safe_edit(event.message, text, markup)
        return
    await event.answer(text, reply_markup=markup)


async def marker_card_text(
    repo: Repo,
    user: User,
    marker_id: int,
    *,
    heading: str | None = None,
) -> str:
    rec = await repo.get_marker(marker_id, user.telegram_id)
    if rec is None:
        return "Метка не найдена или принадлежит другому пользователю."
    stamp = format_dt(parse_iso(rec.occurred_at), user.timezone)
    lines = [f"🔖 <b>{rec.name}</b>", f"Время: {stamp}"]
    if rec.comment:
        lines.append(f"Комментарий: {rec.comment}")
    period = await repo.get_period_for_marker(rec.id, user.telegram_id)
    if period is not None:
        lines.append(_period_summary(user, period))
    body = "\n".join(lines)
    return f"{heading}\n\n{body}" if heading else body


def _period_summary(user: User, period: EventPeriod) -> str:
    title = marker_svc.period_title(period)
    start = format_dt(parse_iso(period.start_at), user.timezone) if period.start_at else "—"
    if period.is_open:
        return f"Период «{title}»: с {start}, конец ещё не отмечен"
    end = format_dt(parse_iso(period.end_at), user.timezone) if period.end_at else "—"
    minutes = None
    if period.start_at and period.end_at:
        minutes = int((parse_iso(period.end_at) - parse_iso(period.start_at)).total_seconds() // 60)
    span = f" ({duration_human(minutes)})" if minutes is not None and minutes >= 0 else ""
    return f"Период «{title}»: {start} — {end}{span}"


def period_card_text(user: User, period: EventPeriod) -> str:
    title = marker_svc.period_title(period)
    lines = [f"▶️ <b>{title}</b>", _period_summary(user, period)]
    if period.start_comment:
        lines.append(f"Начало, комментарий: {period.start_comment}")
    if period.end_comment:
        lines.append(f"Конец, комментарий: {period.end_comment}")
    lines.append("\nУбрать период — метки останутся.")
    return "\n".join(lines)


async def _show_period_card(event: CallbackQuery, user: User, period: EventPeriod) -> None:
    await event.answer()
    await safe_edit(
        event.message,
        period_card_text(user, period),
        period_card_kb(
            period.id,
            can_write(user),
            open_period=period.is_open,
            start_marker_id=period.start_marker_id,
            end_marker_id=period.end_marker_id,
        ),
    )


async def _begin_create(
    cb: CallbackQuery,
    state: FSMContext,
    user: User,
    mode: str,
    *,
    close_period_id: int | None = None,
    same_as: str | None = None,
) -> None:
    await state.set_state(MarkerSG.name)
    payload: dict = {"marker_mode": mode, "tz": user.timezone}
    if close_period_id is not None:
        payload["close_period_id"] = close_period_id
    if same_as:
        payload["same_as_name"] = same_as
    await state.update_data(**payload)
    await cb.answer()
    prompt = NAME_PROMPT
    if mode == "period_start":
        prompt = "Название начала периода? Например: сессия, отпуск."
    elif mode == "period_end":
        prompt = "Название конечной метки?"
    await safe_edit(cb.message, prompt, marker_name_kb(same_as))


@router.callback_query(F.data == NAV_MARKERS)
async def markers_root(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await show_markers_root(cb, repo, user, state)


@router.callback_query(F.data == "mk:new")
async def marker_new(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _begin_create(cb, state, user, "plain")


@router.callback_query(F.data == "mk:start")
async def marker_start(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    await _begin_create(cb, state, user, "period_start")


@router.callback_query(F.data == "mk:end")
async def marker_end_pick(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    periods = await repo.list_open_periods(user.telegram_id)
    if not periods:
        await cb.answer("Нет открытых периодов. Сначала создайте начало.", show_alert=True)
        return
    if len(periods) == 1:
        await _begin_create(
            cb, state, user, "period_end", close_period_id=periods[0].id, same_as=periods[0].start_name
        )
        return
    await state.set_state(MarkerSG.pick_end)
    await cb.answer()
    await safe_edit(cb.message, "Какой период закрыть?", period_pick_kb(periods))


@router.callback_query(F.data.startswith("mk:pe:"))
async def marker_end_chosen(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    period = await repo.get_period(int(cb.data.split(":")[2]), user.telegram_id)
    if period is None or not period.is_open:
        await cb.answer("Этот период уже закрыт или не найден.", show_alert=True)
        return
    await _begin_create(
        cb, state, user, "period_end", close_period_id=period.id, same_as=period.start_name
    )


@router.callback_query(F.data == "mk:samename", MarkerSG.name)
async def marker_same_name(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    name = marker_svc.normalize_name(data.get("same_as_name") or "")
    if not name:
        await cb.answer("Нет названия начала.", show_alert=True)
        return
    await state.update_data(marker_name=name)
    await state.set_state(MarkerSG.comment)
    await cb.answer()
    await safe_edit(cb.message, COMMENT_PROMPT, skip_comment_kb(NAV_MARKERS))


@router.message(MarkerSG.name)
async def marker_got_name(message: Message, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    name = marker_svc.normalize_name(message.text or "")
    if not name:
        await message.answer("Напишите короткое название, например: экзамен.")
        return
    await state.update_data(marker_name=name)
    await state.set_state(MarkerSG.comment)
    await message.answer(COMMENT_PROMPT, reply_markup=skip_comment_kb(NAV_MARKERS))


@router.callback_query(F.data == "wb:skip", MarkerSG.comment)
async def marker_skip_comment(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.update_data(marker_comment=None)
    await cb.answer()
    await safe_edit(cb.message, WHEN_PROMPT, when_kb("mkt"))


@router.message(MarkerSG.comment)
async def marker_got_comment(message: Message, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(message, db_user) is None:
        return
    await state.update_data(marker_comment=marker_svc.normalize_comment(message.text))
    await message.answer(WHEN_PROMPT, reply_markup=when_kb("mkt"))


@router.callback_query(F.data == "mkt:now")
async def marker_now(
    cb: CallbackQuery,
    state: FSMContext,
    repo: Repo,
    db_user: User | None,
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    name = data.get("marker_name")
    if not name:
        await cb.answer("Сначала укажите название.", show_alert=True)
        return
    item_id, error = await marker_svc.add_marker(
        repo,
        user,
        name,
        user_now(user.timezone),
        data.get("marker_comment"),
        as_period_start=data.get("marker_mode") == "period_start",
        close_period_id=data.get("close_period_id"),
    )
    if error:
        await cb.answer(error, show_alert=True)
        return
    await show_saved_entry(cb, repo, user, "mk", item_id, state)


@router.callback_query(F.data == "mkt:time")
async def marker_time(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    if not data.get("marker_name"):
        await cb.answer("Сначала укажите название.", show_alert=True)
        return
    await start_time_pick(cb, state, "mk", _marker_payload(data, user.timezone))


@router.callback_query(F.data == "mk:join")
async def marker_join_start(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    free = await repo.list_unlinked_markers(user.telegram_id)
    if len(free) < 2:
        await cb.answer("Нужны хотя бы две свободные метки (не в периоде).", show_alert=True)
        return
    await state.set_state(MarkerSG.join)
    await state.update_data(join_first=None)
    await cb.answer()
    await safe_edit(
        cb.message,
        "Выберите первую метку. Более ранняя станет началом периода.",
        marker_pick_kb(free, "mk:js", user.timezone),
    )


@router.callback_query(F.data.startswith("mk:js:"), MarkerSG.join)
async def marker_join_pick(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    marker_id = int(cb.data.split(":")[2])
    data = await state.get_data()
    first_id = data.get("join_first")
    if first_id is None or first_id == marker_id:
        first_id = None if first_id == marker_id else marker_id
        await state.update_data(join_first=first_id)
        free = await repo.list_unlinked_markers(user.telegram_id)
        await cb.answer()
        prompt = (
            "Выберите вторую метку."
            if first_id is not None
            else "Выберите первую метку. Более ранняя станет началом периода."
        )
        await safe_edit(
            cb.message,
            prompt,
            marker_pick_kb(free, "mk:js", user.timezone, selected_id=first_id),
        )
        return
    period_id, error = await marker_svc.link_markers(repo, user, int(first_id), marker_id)
    if error:
        await cb.answer(error, show_alert=True)
        return
    period = await repo.get_period(period_id, user.telegram_id) if period_id else None
    await state.clear()
    if period is None:
        await cb.answer("Связано")
        await show_markers_root(cb, repo, user)
        return
    await _show_period_card(cb, user, period)


@router.callback_query(F.data.startswith("mk:o:"))
async def marker_open(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    marker_id = int(cb.data.split(":")[2])
    rec = await repo.get_marker(marker_id, user.telegram_id)
    if rec is None:
        await cb.answer("Метка не найдена", show_alert=True)
        return
    await _show_marker_card(cb, repo, user, marker_id)


@router.callback_query(F.data.startswith("mk:p:"))
async def period_open(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    period = await repo.get_period(int(cb.data.split(":")[2]), user.telegram_id)
    if period is None:
        await cb.answer("Период не найден", show_alert=True)
        return
    await _show_period_card(cb, user, period)


@router.callback_query(F.data.startswith("mk:u:"))
async def unlink_ask(cb: CallbackQuery, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    period_id = int(cb.data.split(":")[2])
    period = await repo.get_period(period_id, user.telegram_id)
    if period is None:
        await cb.answer("Период не найден", show_alert=True)
        return
    await cb.answer()
    await safe_edit(
        cb.message,
        f"Убрать период «{marker_svc.period_title(period)}»? Метки останутся.",
        confirm_unlink_kb(period_id),
    )


@router.callback_query(F.data.startswith("mk:uok:"))
async def unlink_ok(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    error = await marker_svc.unlink_period(repo, user, int(cb.data.split(":")[2]))
    if error:
        await cb.answer(error, show_alert=True)
        return
    await cb.answer("Период убран, метки на месте")
    await show_markers_root(cb, repo, user, state)


@router.callback_query(F.data.startswith("mk:nm:"))
async def marker_edit_name(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    marker_id = int(cb.data.split(":")[2])
    rec = await repo.get_marker(marker_id, user.telegram_id)
    if rec is None:
        await cb.answer("Метка не найдена", show_alert=True)
        return
    await state.set_state(MarkerSG.edit_name)
    await state.update_data(edit_marker_id=marker_id)
    await cb.answer()
    await safe_edit(cb.message, f"Сейчас: {rec.name}\nНовое название:", marker_name_kb())


@router.message(MarkerSG.edit_name)
async def marker_save_name(
    message: Message, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    name = marker_svc.normalize_name(message.text or "")
    if not name:
        await message.answer("Напишите короткое название.")
        return
    data = await state.get_data()
    marker_id = int(data["edit_marker_id"])
    rec = await repo.get_marker(marker_id, user.telegram_id)
    if rec is None:
        await message.answer("Метка не найдена.")
        return
    await repo.update_marker(marker_id, user.telegram_id, name=name)
    await _show_marker_card(message, repo, user, marker_id, state, heading="✅ Название обновлено")


@router.callback_query(F.data.startswith("mk:cm:"))
async def marker_edit_comment(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    marker_id = int(cb.data.split(":")[2])
    rec = await repo.get_marker(marker_id, user.telegram_id)
    if rec is None:
        await cb.answer("Метка не найдена", show_alert=True)
        return
    await state.set_state(MarkerSG.edit_comment)
    await state.update_data(edit_marker_id=marker_id)
    await cb.answer()
    await safe_edit(
        cb.message,
        "Новый комментарий. Можно пропустить — тогда комментарий сотрётся.",
        skip_comment_kb(f"mk:o:{marker_id}"),
    )


@router.callback_query(F.data == "wb:skip", MarkerSG.edit_comment)
async def marker_clear_comment(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    data = await state.get_data()
    marker_id = int(data["edit_marker_id"])
    await repo.update_marker(marker_id, user.telegram_id, comment=None)
    await _show_marker_card(
        cb, repo, user, marker_id, state, heading="✅ Комментарий убран", toast="Сохранено"
    )


@router.message(MarkerSG.edit_comment)
async def marker_save_comment(
    message: Message, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(message, db_user)
    if user is None:
        return
    data = await state.get_data()
    marker_id = int(data["edit_marker_id"])
    await repo.update_marker(
        marker_id, user.telegram_id, comment=marker_svc.normalize_comment(message.text)
    )
    await _show_marker_card(message, repo, user, marker_id, state, heading="✅ Комментарий обновлён")
