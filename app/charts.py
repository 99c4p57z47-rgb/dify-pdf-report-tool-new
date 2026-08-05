"""Validated, source-attributed chart rendering for strategic reports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.font_manager import FontProperties
from PIL import Image as PILImage

if TYPE_CHECKING:
    from app.fonts import FontRegistry
    from app.models import ChartSpec


DEFAULT_PALETTE = (
    "#2F6B8A",
    "#2A9D8F",
    "#E9C46A",
    "#E76F51",
    "#6D597A",
    "#7A9E7E",
)


class ChartDataError(ValueError):
    """Raised when structured chart data cannot be truthfully rendered."""


@dataclass(frozen=True)
class ChartResult:
    path: Path
    width_px: int
    height_px: int
    warnings: tuple[str, ...] = ()


def validate_chart(spec: ChartSpec) -> None:
    """Validate the numeric, attribution, and chart-type constraints for *spec*."""
    if not spec.source.strip():
        raise ChartDataError("图表必须提供可读来源")
    if not spec.source_ids or not all(source_id.strip() for source_id in spec.source_ids):
        raise ChartDataError("图表必须提供有效 source_ids")
    if len(spec.source_ids) != len(set(spec.source_ids)):
        raise ChartDataError("图表 source_ids 不可重复")

    for dataset in spec.datasets:
        if len(dataset.data) != len(spec.labels):
            raise ChartDataError("图表标签与数据数量不一致")
        if not all(math.isfinite(value) for value in dataset.data):
            raise ChartDataError("图表数据必须为有限数值")

    if spec.type not in {"pie", "doughnut"}:
        return

    if len(spec.datasets) != 1:
        raise ChartDataError("饼图/环形图只能包含一个数据集")
    values = spec.datasets[0].data
    if not 2 <= len(values) <= 5:
        raise ChartDataError("饼图/环形图必须包含 2–5 个数据点")
    if any(value < 0 for value in values):
        raise ChartDataError("饼图/环形图数据必须为非负数")
    if sum(values) <= 0:
        raise ChartDataError("饼图/环形图数据总和必须大于 0")
    if spec.unit == "%" and not 99.5 <= sum(values) <= 100.5:
        raise ChartDataError("百分比饼图/环形图总计必须约为 100")


def choose_chart_orientation(spec: ChartSpec) -> str:
    """Return the readable rendering type after applying bar-label safeguards."""
    if spec.type == "bar" and (len(spec.labels) > 8 or any(len(label) > 12 for label in spec.labels)):
        return "horizontal_bar"
    return spec.type


def _font(fonts: FontRegistry) -> FontProperties:
    return FontProperties(fname=str(fonts.regular_path))


def _color_for(color_map: dict[str, str], key: str) -> str:
    if key not in color_map:
        color_map[key] = DEFAULT_PALETTE[len(color_map) % len(DEFAULT_PALETTE)]
    return color_map[key]


def _set_font(text_items, font: FontProperties) -> None:
    for item in text_items:
        item.set_fontproperties(font)


def render_chart(
    spec: ChartSpec,
    output_path: Path,
    fonts: FontRegistry,
    color_map: dict[str, str],
) -> ChartResult:
    """Render validated structured values to *output_path*, without a chart title.

    Titles belong to the report layout component so they are extractable and
    never duplicated beneath the chart image.
    """
    validate_chart(spec)
    rendered_type = choose_chart_orientation(spec)
    warnings: list[str] = []
    if rendered_type != spec.type:
        warnings.append(f"图表《{spec.title}》标签较长或类别较多，已改为横向条形图")

    font = _font(fonts)
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=160)
    try:
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        labels = spec.labels
        x = np.arange(len(labels))

        if rendered_type in {"pie", "doughnut"}:
            values = spec.datasets[0].data
            colors = [_color_for(color_map, label) for label in labels]
            _, _, autotexts = ax.pie(
                values,
                labels=labels,
                autopct=lambda pct: f"{pct:.1f}%" if pct >= 2 else "",
                startangle=90,
                colors=colors,
                wedgeprops={"width": 0.48 if rendered_type == "doughnut" else 1.0, "edgecolor": "white"},
                textprops={"fontsize": 9},
            )
            _set_font(autotexts, font)
            _set_font(ax.texts, font)
            ax.axis("equal")
        elif rendered_type == "horizontal_bar":
            height = 0.75 / len(spec.datasets)
            offsets = (np.arange(len(spec.datasets)) - (len(spec.datasets) - 1) / 2) * height
            for index, dataset in enumerate(spec.datasets):
                bars = ax.barh(
                    x + offsets[index],
                    dataset.data,
                    height=height,
                    color=_color_for(color_map, dataset.label),
                    label=dataset.label,
                )
                ax.bar_label(bars, padding=3, fontsize=8)
            ax.set_yticks(x, labels)
            ax.invert_yaxis()
            ax.grid(axis="x", color="#D9E2E8", linewidth=0.7, alpha=0.8)
        elif rendered_type == "bar":
            width = 0.78 / len(spec.datasets)
            offsets = (np.arange(len(spec.datasets)) - (len(spec.datasets) - 1) / 2) * width
            for index, dataset in enumerate(spec.datasets):
                bars = ax.bar(
                    x + offsets[index],
                    dataset.data,
                    width=width,
                    color=_color_for(color_map, dataset.label),
                    label=dataset.label,
                )
                ax.bar_label(bars, padding=3, fontsize=7.5)
            ax.set_xticks(x, labels, rotation=25 if len(labels) > 6 else 0, ha="right" if len(labels) > 6 else "center")
            ax.grid(axis="y", color="#D9E2E8", linewidth=0.7, alpha=0.8)
        else:
            for dataset in spec.datasets:
                ax.plot(
                    x,
                    dataset.data,
                    marker="o",
                    linewidth=2.2,
                    color=_color_for(color_map, dataset.label),
                    label=dataset.label,
                )
            ax.set_xticks(x, labels, rotation=25 if len(labels) > 6 else 0, ha="right" if len(labels) > 6 else "center")
            ax.grid(color="#D9E2E8", linewidth=0.7, alpha=0.8)

        if rendered_type not in {"pie", "doughnut"}:
            ax.spines[["top", "right"]].set_visible(False)
            if spec.unit:
                ax.set_ylabel(spec.unit, fontproperties=font)
            if len(spec.datasets) > 1 or spec.datasets[0].label:
                ax.legend(frameon=False, prop=font, loc="best")
            _set_font(list(ax.get_xticklabels()) + list(ax.get_yticklabels()), font)

        fig.tight_layout()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight", facecolor="white")
        with PILImage.open(output_path) as image:
            width_px, height_px = image.size
        return ChartResult(output_path, int(width_px), int(height_px), tuple(warnings))
    finally:
        plt.close(fig)


__all__ = [
    "ChartDataError",
    "ChartResult",
    "DEFAULT_PALETTE",
    "choose_chart_orientation",
    "render_chart",
    "validate_chart",
]
