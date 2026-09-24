from __future__ import annotations

from services.load_charts import render_load_chart


def test_render_load_chart_draws_all_three_series():
    samples = [
        ("2026-09-24T08:00:00+00:00", 0.2, 0.4, 0.6),
        ("2026-09-24T08:01:00+00:00", 0.5, 0.45, 0.55),
        ("2026-09-24T08:02:00+00:00", 1.2, 0.8, 0.7),
    ]
    png = render_load_chart(samples, "час", "Europe/Moscow")
    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert render_load_chart([], "час", "Europe/Moscow") is None
