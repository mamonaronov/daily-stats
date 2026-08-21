from __future__ import annotations

import asyncio
from types import SimpleNamespace

from middlewares import DrainMiddleware
from utils.deploy_drain import drain_paths, format_drain_status, watch_deploy_drain
from utils.runtime import RuntimeControl, hold, set_runtime


async def test_hold_and_wait_idle_quiet_window():
    runtime = RuntimeControl()
    set_runtime(runtime)
    try:
        async with runtime.hold("backup"):
            assert runtime.blockers() == {"backup": 1}
            assert runtime.is_idle() is False
        assert runtime.is_idle() is True
        assert await runtime.wait_idle(timeout=0.8, quiet=0.2) is True
    finally:
        set_runtime(None)


async def test_wait_idle_false_while_hold_open():
    runtime = RuntimeControl()

    async def linger() -> None:
        async with runtime.hold("handler"):
            await asyncio.sleep(1)

    task = asyncio.create_task(linger())
    await asyncio.sleep(0.05)
    assert await runtime.wait_idle(timeout=0.2, quiet=0.05) is False
    await task
    assert await runtime.wait_idle(timeout=0.6, quiet=0.1) is True


async def test_nested_holds_same_reason():
    runtime = RuntimeControl()
    async with runtime.hold("backup"):
        async with runtime.hold("backup"):
            assert runtime.blockers() == {"backup": 2}
        assert runtime.blockers() == {"backup": 1}
    assert runtime.is_idle()


async def test_module_hold_is_noop_without_runtime():
    set_runtime(None)
    async with hold("backup"):
        pass


async def test_drain_middleware_rejects_new_work():
    runtime = RuntimeControl()
    runtime.begin_drain()
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True
        return "ok"

    result = await DrainMiddleware()(handler, SimpleNamespace(), {"runtime": runtime})
    assert result is None
    assert called is False


async def test_drain_middleware_tracks_handler_hold():
    runtime = RuntimeControl()

    async def handler(_event, _data):
        assert runtime.blockers() == {"handler": 1}
        return "ok"

    result = await DrainMiddleware()(handler, SimpleNamespace(), {"runtime": runtime})
    assert result == "ok"
    assert runtime.is_idle()


def test_format_drain_status():
    runtime = RuntimeControl()
    assert format_drain_status(runtime) == "idle\n"
    runtime._holds["backup"] = 1
    runtime._holds["handler"] = 2
    text = format_drain_status(runtime)
    assert text.startswith("busy ")
    assert "backup=1" in text
    assert "handler=2" in text


async def test_watch_writes_idle_after_backup_finishes(tmp_path):
    runtime = RuntimeControl()
    stop = asyncio.Event()
    request, idle, status = drain_paths(tmp_path)
    task = asyncio.create_task(watch_deploy_drain(runtime, tmp_path, stop))
    await asyncio.sleep(0.2)
    try:
        async with runtime.hold("backup"):
            request.write_text("1\n", encoding="utf-8")
            for _ in range(20):
                if status.is_file() and "backup=" in status.read_text(encoding="utf-8"):
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("drain status was not written while backup hold was open")
            assert not idle.is_file()
            assert runtime.draining is True
        for _ in range(40):
            if idle.is_file():
                break
            await asyncio.sleep(0.2)
        assert idle.read_text(encoding="utf-8") == "ok\n"
        assert status.read_text(encoding="utf-8") == "idle\n"
    finally:
        stop.set()
        await task
