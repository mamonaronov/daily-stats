"""Good decisions: their own menu, separate from custom metrics."""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from database.models import User
from database.queries import Repo
from handlers.common import require_active, require_writable
from handlers.custom_metrics import _load_pledge, _run_pledge_action
from keyboards.main import back_kb, pledges_hub_kb
from services.pledges import pledge_edge_dates
from services.users import can_write
from states.diary import CustomMetricSG
from utils.callbacks import NAV_PLEDGES
from utils.telegram import safe_edit
from utils.time import format_date, user_today

router = Router(name="pledges")

PLEDGES_EMPTY = (
    "📆 <b>Хорошие решения</b>\n\n"
    "Это обещание на выбранные дни: читать, бегать, не курить.\n\n"
    "Когда день сделан, нажмите кнопку с датой. "
    "Засчитывается самый ранний ещё не отмеченный день, не дальше сегодня.\n\n"
    "Пока пусто. Нажмите «Создать решение»."
)
PLEDGES_HELP = (
    "В строке решения:\n"
    "• <b>название</b> — карточка: сколько сделано и отметить все дни до сегодня\n"
    "• <b>дата</b> — засчитать этот день сделанным\n"
    "• <b>↩ дата</b> — снять последнюю отметку, если отметили день, который ещё не сделан\n\n"
    "Засчитывается самый ранний ещё не отмеченный день, не дальше сегодня."
)
NAME_PROMPT = "Как назвать хорошее решение?\n\nНапример: читать, бегать."


def _start_day(metric) -> date | None:
    raw = getattr(metric, "starts_on", None)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def pledges_menu_text(metrics, pledge_next: dict[int, date], today: date) -> str:
    if not metrics:
        return PLEDGES_EMPTY
    lines = ["📆 <b>Хорошие решения</b>", "", PLEDGES_HELP, ""]
    for metric in metrics:
        name = metric.name or "Решение"
        nxt = pledge_next.get(metric.id) if metric.enabled else None
        if not metric.enabled:
            lines.append(f"• <b>{name}</b> — выключено")
        elif nxt is not None:
            lines.append(f"• <b>{name}</b> — можно отметить {format_date(nxt)}")
        else:
            start = _start_day(metric)
            if start is not None and start > today:
                lines.append(f"• <b>{name}</b> — начнётся {format_date(start)}")
            else:
                lines.append(f"• <b>{name}</b> — до сегодня всё отмечено")
    return "\n".join(lines)


async def show_pledges(target: CallbackQuery, repo: Repo, user: User, state: FSMContext | None = None) -> None:
    if state:
        await state.clear()
    metrics = [item for item in await repo.list_metrics(user.telegram_id) if item.data_type == "pledge"]
    pledge_next, pledge_undo = await pledge_edge_dates(repo, user, metrics)
    text = pledges_menu_text(metrics, pledge_next, user_today(user.timezone))
    markup = pledges_hub_kb(metrics, can_write(user), pledge_next=pledge_next, pledge_undo=pledge_undo)
    await safe_edit(target.message, text, markup)


@router.callback_query(F.data == NAV_PLEDGES)
async def pledges_root(cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None) -> None:
    user = await require_active(cb, db_user)
    if user is None:
        return
    await cb.answer()
    await show_pledges(cb, repo, user, state)


@router.callback_query(F.data == "pl:new")
async def pledge_new(cb: CallbackQuery, state: FSMContext, db_user: User | None) -> None:
    if await require_writable(cb, db_user) is None:
        return
    await state.clear()
    await state.set_state(CustomMetricSG.name)
    await state.update_data(flow="pledge")
    await cb.answer()
    await safe_edit(cb.message, NAME_PROMPT, back_kb(NAV_PLEDGES))


@router.callback_query(F.data.startswith("pl:q:"))
async def pledge_close_from_hub(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric = await _load_pledge(cb, repo, user)
    if metric is None:
        return
    notice = await _run_pledge_action(cb, repo, user, metric, "next")
    if notice and notice.startswith("Закрыто"):
        await cb.answer(notice)
        await show_pledges(cb, repo, user, state)
        return
    await cb.answer(notice or "Не получилось", show_alert=True)


@router.callback_query(F.data.startswith("pl:u:"))
async def pledge_undo_from_hub(
    cb: CallbackQuery, state: FSMContext, repo: Repo, db_user: User | None
) -> None:
    user = await require_writable(cb, db_user)
    if user is None:
        return
    metric = await _load_pledge(cb, repo, user)
    if metric is None:
        return
    notice = await _run_pledge_action(cb, repo, user, metric, "undo")
    if notice and notice.startswith("Снято"):
        await cb.answer(notice)
        await show_pledges(cb, repo, user, state)
        return
    await cb.answer(notice or "Не получилось", show_alert=True)
