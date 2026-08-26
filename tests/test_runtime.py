from __future__ import annotations

from utils.runtime import RuntimeControl, get_runtime, set_runtime


def test_request_stop_sets_event_and_kicks():
    runtime = RuntimeControl()
    kicked = []
    runtime.bind(lambda: kicked.append(True))
    runtime.request_stop()
    assert runtime.stop.is_set()
    assert kicked == [True]


def test_request_restart_sets_flag_and_stops():
    runtime = RuntimeControl()
    runtime.request_restart()
    assert runtime.restart is True
    assert runtime.stop.is_set()


def test_get_set_runtime():
    runtime = RuntimeControl()
    set_runtime(runtime)
    try:
        assert get_runtime() is runtime
    finally:
        set_runtime(None)
    assert get_runtime() is None
