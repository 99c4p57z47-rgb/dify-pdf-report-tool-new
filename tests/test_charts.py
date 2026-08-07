from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image as PILImage

from app.fonts import FontRegistry
from app.models import ChartSpec, DatasetSpec


@pytest.fixture
def fonts() -> FontRegistry:
    import matplotlib

    font_path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
    return FontRegistry(
        regular_name="DejaVu Sans",
        bold_name="DejaVu Sans",
        regular_path=font_path,
        bold_path=font_path,
    )


@pytest.fixture
def chart_spec() -> ChartSpec:
    return ChartSpec(
        type="bar",
        title="渠道销售额",
        labels=["线上", "线下"],
        datasets=[DatasetSpec(label="销售额", data=[40, 60])],
        unit="亿元",
        source="国家统计局《零售统计公报》",
        source_ids=["nbs-2025"],
    )


@pytest.fixture
def pie_spec() -> ChartSpec:
    return ChartSpec(
        type="pie",
        title="渠道占比",
        labels=["线上", "线下"],
        datasets=[DatasetSpec(label="占比", data=[45, 55])],
        unit="%",
        source="国家统计局《零售统计公报》",
        source_ids=["nbs-2025"],
    )


@pytest.fixture
def long_label_spec(chart_spec: ChartSpec) -> ChartSpec:
    chart_spec.labels = ["超过十二个汉字的渠道分类标签", "常规渠道"]
    return chart_spec


def test_chart_rejects_label_data_mismatch(chart_spec: ChartSpec) -> None:
    """Removing one data point must not produce a falsely aligned chart."""
    from app.charts import ChartDataError, validate_chart

    chart_spec.datasets[0].data = [1]

    with pytest.raises(ChartDataError, match="数量不一致"):
        validate_chart(chart_spec)


def test_pie_requires_total_100_percent(pie_spec: ChartSpec) -> None:
    """A percentage pie whose total is not 100 must be rejected."""
    from app.charts import ChartDataError, validate_chart

    pie_spec.datasets[0].data = [20, 30]

    with pytest.raises(ChartDataError, match="100"):
        validate_chart(pie_spec)


def test_pie_rejects_an_all_zero_total(pie_spec: ChartSpec) -> None:
    """An all-zero pie cannot represent proportions and must not reach matplotlib."""
    from app.charts import ChartDataError, validate_chart

    pie_spec.unit = ""
    pie_spec.datasets[0].data = [0, 0]

    with pytest.raises(ChartDataError, match="总和"):
        validate_chart(pie_spec)


def test_long_labels_choose_horizontal_bar(long_label_spec: ChartSpec) -> None:
    """A vertical bar chart with an unreadable label must switch orientation."""
    from app.charts import choose_chart_orientation

    assert choose_chart_orientation(long_label_spec) == "horizontal_bar"


def test_chart_requires_human_readable_source(chart_spec: ChartSpec) -> None:
    """Removing the displayed source must make final-report chart validation fail."""
    from app.charts import ChartDataError, validate_chart

    chart_spec.source = "  "

    with pytest.raises(ChartDataError, match="来源"):
        validate_chart(chart_spec)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_chart_rejects_non_finite_values(chart_spec: ChartSpec, value: float) -> None:
    """A non-finite data point must never reach the renderer."""
    from app.charts import ChartDataError, validate_chart

    chart_spec.datasets[0].data = [40, value]

    with pytest.raises(ChartDataError, match="有限"):
        validate_chart(chart_spec)


@pytest.mark.parametrize(
    ("datasets", "data", "error"),
    [
        ([DatasetSpec(label="A", data=[50, 50]), DatasetSpec(label="B", data=[50, 50])], None, "一个数据集"),
        (None, [50, -50], "非负"),
        (None, [10, 20, 30, 40, 0, 0], "2–5"),
    ],
)
def test_pie_rejects_invalid_shape(
    pie_spec: ChartSpec,
    datasets: list[DatasetSpec] | None,
    data: list[float] | None,
    error: str,
) -> None:
    """Pie inputs outside the supported single-series 2–5 slice shape are unsafe."""
    from app.charts import ChartDataError, validate_chart

    if datasets is not None:
        pie_spec.datasets = datasets
    if data is not None:
        pie_spec.labels = [str(index) for index in range(len(data))]
        pie_spec.datasets[0].data = data

    with pytest.raises(ChartDataError, match=error):
        validate_chart(pie_spec)


def test_render_chart_applies_series_colour_map_and_returns_dimensions(
    chart_spec: ChartSpec,
    fonts: FontRegistry,
    tmp_path: Path,
) -> None:
    """Changing a report-wide series color must change the pixels of the rendered chart."""
    from app.charts import render_chart

    output_path = tmp_path / "chart.png"
    result = render_chart(
        chart_spec,
        output_path,
        fonts,
        {"销售额": "#FF00FF"},
    )

    assert result.path == output_path
    assert result.width_px > 0
    assert result.height_px > 0
    assert result.warnings == ()
    with PILImage.open(output_path).convert("RGB") as image:
        assert (255, 0, 255) in image.getdata()


def test_render_chart_closes_figure_when_saving_fails(
    chart_spec: ChartSpec,
    fonts: FontRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A save failure must not leave a matplotlib figure open in batch rendering."""
    from app import charts

    original_subplots = charts.plt.subplots
    created_figure_numbers: list[int] = []

    def subplots_with_failing_save(*args, **kwargs):
        figure, axes = original_subplots(*args, **kwargs)
        created_figure_numbers.append(figure.number)

        def savefig_failure(*_args, **_kwargs):
            raise OSError("disk unavailable")

        monkeypatch.setattr(figure, "savefig", savefig_failure)
        return figure, axes

    before = set(charts.plt.get_fignums())
    monkeypatch.setattr(charts.plt, "subplots", subplots_with_failing_save)

    with pytest.raises(OSError, match="disk unavailable"):
        charts.render_chart(chart_spec, tmp_path / "chart.png", fonts, {})

    assert created_figure_numbers
    assert created_figure_numbers[0] not in charts.plt.get_fignums()
    assert set(charts.plt.get_fignums()) == before
