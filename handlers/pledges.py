"""Good decisions: their own menu, separate from custom metrics."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from database.models import User
from database.queries import Repo
from handlers.common import require_active, require_writable
from handlers.custom_metrics import _load_pledge, _run_pledge_action
from keyboards.main import back_kb, pledges_hub_kb
from services.pledges import next_open_dates
from services.users import can_write
from states.diary import CustomMetricSG
from utils.callbacks import NAV_PLEDGES
from utils.telegram import safe_edit

router = Router(name="pledges")

PLEDGES_EMPTY = (
    "📆 <b>Хорошие решения</b>\n\n"
    "Обязательство на даты: каждый день или выбранные дни, с начала и по желанию до конца. "
    "Отметка закрывает самую раннюю открытую дату и не заходит дальше сегодня.\n\n"
    "Пока нет решений. Создайте первое."
)
PLEDGES_LIST = (
    "📆 <b>Хорошие решения</b>\n\n"
    "Дата закрывает следующий открытый день. Название открывает решение."
)
NAME_PROMPT = "Как назвать хорошее решение?\n\nНапример: читать, бегать."


async def show_pledges(target: CallbackQuery, repo: Repo, user: User, state: FSMContext | None = None) -> None:
    if state:
        await state.clear()
    metrics = [item for item in await repo.list_metrics(user.telegram_id) if item.data_type == "pledge"]
    pledge_next = await next_open_dates(repo, user, metrics)
    text = PLEDGES_LIST if metrics else PLEDGES_EMPTY
    markup = pledges_hub_kb(metrics, can_write(user), pledge_next=pledge_next)
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
