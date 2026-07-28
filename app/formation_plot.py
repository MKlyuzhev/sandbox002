"""Candlestick chart rendering for formation analysis overlays.

Takes the same bars fed to ``patterns.analyze_bars`` plus the analysis dict;
writes a PNG. Does not change pattern detection. Uses the Agg backend so CLI
and tests need no display.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402


class PlotError(ValueError):
    """Raised when a formation chart cannot be rendered."""


def _draw_candles(ax: Any, bars: list[dict]) -> None:
    width = 0.6
    for i, b in enumerate(bars):
        o, h, l, c = b["open"], b["high"], b["low"], b["close"]
        up = c >= o
        color = "#2ca02c" if up else "#d62728"
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, solid_capstyle="round")
        body_bottom = min(o, c)
        body_height = abs(c - o) or (h - l) * 0.01 or 1e-6
        ax.add_patch(
            Rectangle(
                (i - width / 2, body_bottom),
                width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
            )
        )


def _sparse_time_ticks(ax: Any, bars: list[dict], max_ticks: int = 8) -> None:
    n = len(bars)
    if n == 0:
        return
    step = max(1, n // max_ticks)
    indices = list(range(0, n, step))
    if indices[-1] != n - 1:
        indices.append(n - 1)
    labels = []
    for i in indices:
        t = bars[i].get("time") or ""
        # OANDA RFC3339 → show date + hour when present
        label = str(t)[:16].replace("T", " ") if t else str(i)
        labels.append(label)
    ax.set_xticks(indices)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)


def plot_formation(
    bars: list[dict],
    analysis: dict,
    path: str | Path,
    title: str | None = None,
) -> Path:
    """Render candlesticks + detected overlays; save PNG; return path."""
    if not bars:
        raise PlotError("bars must be non-empty")
    for i, b in enumerate(bars):
        for key in ("open", "high", "low", "close"):
            if key not in b:
                raise PlotError(f"bar[{i}] missing '{key}'")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 6))
    _draw_candles(ax, bars)

    # Swings
    for p in analysis.get("swings") or []:
        idx = p.get("index")
        price = p.get("price")
        if idx is None or price is None:
            continue
        if p.get("kind") == "high":
            ax.scatter([idx], [price], marker="v", color="#1f77b4", s=36, zorder=5)
        else:
            ax.scatter([idx], [price], marker="^", color="#ff7f0e", s=36, zorder=5)

    # Trendlines (pivot segment + extension to last bar)
    last_i = len(bars) - 1
    for ln in analysis.get("trendlines") or []:
        i0, i1 = ln.get("i0"), ln.get("i1")
        p0, p1 = ln.get("price0"), ln.get("price1")
        if None in (i0, i1, p0, p1):
            continue
        color = "#2ca02c" if ln.get("kind") == "support" else "#d62728"
        ax.plot([i0, i1], [p0, p1], color=color, linewidth=1.2, alpha=0.7, zorder=4)
        at_last = ln.get("price_at_last")
        if at_last is not None and i1 < last_i:
            ax.plot(
                [i1, last_i],
                [p1, at_last],
                color=color,
                linewidth=1.0,
                linestyle="--",
                alpha=0.5,
                zorder=4,
            )

    hs = analysis.get("hs") or {}
    # H&S landmarks
    for key, label, color in (
        ("left_shoulder", "LS", "#9467bd"),
        ("head", "H", "#e377c2"),
        ("right_shoulder", "RS", "#8c564b"),
    ):
        pt = hs.get(key)
        if not pt:
            continue
        idx, price = pt.get("index"), pt.get("price")
        if idx is None or price is None:
            continue
        ax.scatter([idx], [price], marker="o", s=50, color=color, zorder=6)
        ax.annotate(
            label,
            (idx, price),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=9,
            color=color,
            fontweight="bold",
        )

    # Neckline
    nl = hs.get("neckline")
    if nl:
        i0, i1 = nl.get("i0"), nl.get("i1")
        p0, p1 = nl.get("price0"), nl.get("price1")
        if None not in (i0, i1, p0, p1):
            ax.plot(
                [i0, i1],
                [p0, p1],
                color="#17becf",
                linewidth=2.0,
                label="neckline",
                zorder=5,
            )
            at_last = nl.get("price_at_last")
            if at_last is not None and i1 < last_i:
                ax.plot(
                    [i1, last_i],
                    [p1, at_last],
                    color="#17becf",
                    linewidth=1.5,
                    linestyle="--",
                    zorder=5,
                )

    # Min target on confirmed break
    if hs.get("stage") == "confirmed_break" and hs.get("min_target") is not None:
        ax.axhline(
            float(hs["min_target"]),
            color="#7f7f7f",
            linestyle=":",
            linewidth=1.5,
            label="min_target",
        )

    instrument = analysis.get("instrument") or ""
    granularity = analysis.get("granularity") or ""
    stage = hs.get("stage") or "none"
    last_close = analysis.get("last_close")
    if title is None:
        title = f"{instrument} {granularity}".strip() or "Formation analysis"
    ax.set_title(f"{title}  |  stage={stage}  last={last_close}")
    ax.set_ylabel("Price")
    ax.set_xlabel("Bar index")
    _sparse_time_ticks(ax, bars)
    ax.grid(True, alpha=0.25)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="best", fontsize=8)

    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out
