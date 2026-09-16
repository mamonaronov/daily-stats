"""Shared dark theme for matplotlib charts in the bot."""

from __future__ import annotations

BG = "#111318"
FG = "#e8eaed"
GRID = "#3a3f4b"
AXIS = "#8b919a"
LEGEND_BG = "#1c1f26"
LINE = "#7dd3fc"
BAR = "#3b82f6"
BAR_ALT = "#2dd4bf"


def apply_dark(fig, *axes, grid: str | bool | None = "y") -> None:
    fig.patch.set_facecolor(BG)
    axis = None if not grid else ("y" if grid is True else grid)
    for ax in axes:
        ax.set_facecolor(BG)
        ax.tick_params(colors=FG, labelsize=10)
        ax.xaxis.label.set_color(FG)
        ax.yaxis.label.set_color(FG)
        ax.title.set_color(FG)
        for spine in ax.spines.values():
            spine.set_color(AXIS)
        if axis == "both":
            ax.grid(True, color=GRID, alpha=0.75)
        elif axis:
            ax.grid(True, axis=axis, color=GRID, alpha=0.75)


def style_legend(legend) -> None:
    if legend is None:
        return
    frame = legend.get_frame()
    frame.set_facecolor(LEGEND_BG)
    frame.set_edgecolor(GRID)
    for text in legend.get_texts():
        text.set_color(FG)


def save_png(fig, buf, **kwargs) -> None:
    kwargs.setdefault("facecolor", fig.get_facecolor())
    kwargs.setdefault("edgecolor", "none")
    fig.savefig(buf, **kwargs)
