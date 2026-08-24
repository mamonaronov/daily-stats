"""Router assembly. One dispatcher, one bot."""

from __future__ import annotations

from aiogram import Router

from handlers import (
    activity,
    admin,
    admin_db,
    admin_deploy,
    admin_restore,
    alcohol,
    caffeine,
    cigarettes,
    custom_metrics,
    fooling,
    guide,
    history,
    legal,
    markers,
    menu,
    settings,
    sleep,
    snus,
    start,
    statistics,
    time_pick,
)


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(legal.router)
    root.include_router(menu.router)
    root.include_router(guide.router)
    root.include_router(admin.router)
    root.include_router(admin_deploy.router)
    root.include_router(admin_restore.router)
    root.include_router(admin_db.router)
    root.include_router(time_pick.router)
    root.include_router(cigarettes.router)
    root.include_router(fooling.router)
    root.include_router(snus.router)
    root.include_router(sleep.router)
    root.include_router(caffeine.router)
    root.include_router(alcohol.router)
    root.include_router(activity.router)
    root.include_router(history.router)
    root.include_router(markers.router)
    root.include_router(statistics.router)
    root.include_router(settings.router)
    root.include_router(custom_metrics.router)
    return root
