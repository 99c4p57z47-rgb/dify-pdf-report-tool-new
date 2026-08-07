from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from PIL import Image as PILImage
from pypdf import PdfReader
from pypdf.generic import ContentStream

from app.assets import AssetManifestError, AssetRegistry, ResolvedImage
from app.fonts import FontRegistry
from app.layout import EvidenceImage, ReportTheme, StrategicReportBuilder
from app.models import (
    ChartSpec,
    DatasetSpec,
    ExecutiveInsightSpec,
    ImageSpec,
    ReportRequest,
    SectionSpec,
    SourceSpec,
)


@pytest.fixture
def theme() -> ReportTheme:
    return ReportTheme(regular_font="Helvetica", bold_font="Helvetica-Bold")


@pytest.fixture
def fonts(tmp_path: Path) -> FontRegistry:
    return FontRegistry(
        regular_name="Helvetica",
        bold_name="Helvetica-Bold",
        regular_path=tmp_path / "regular.ttf",
        bold_path=tmp_path / "bold.ttf",
    )


@pytest.fixture
def builder(fonts: FontRegistry, theme: ReportTheme) -> StrategicReportBuilder:
    return StrategicReportBuilder(fonts, theme)


@pytest.fixture
def resolved_images(tmp_path: Path) -> dict[tuple[int, int], ResolvedImage]:
    portrait = tmp_path / "portrait.png"
    landscape = tmp_path / "landscape.png"
    PILImage.new("RGB", (500, 1200), "#DDEDEA").save(portrait)
    PILImage.new("RGB", (1400, 500), "#E7EEF2").save(landscape)
    return {
        (0, 0): ResolvedImage(
            path=portrait,
            caption="Trusted portrait evidence caption",
            report_title="Trusted Source Report",
            publisher="Evidence Institute",
            year=2025,
            source_page=11,
        ),
        (0, 1): ResolvedImage(
            path=landscape,
            caption="Trusted landscape evidence caption",
            report_title="Trusted Source Report",
            publisher="Evidence Institute",
            year=2025,
            source_page=12,
        ),
    }


@pytest.fixture
def stress_payload() -> ReportRequest:
    headings = "Strategic market outlook " + ("long horizon choices " * 7)
    header = "| Segment | Demand signal | Competitive response | Channel | Risk | Owner | Timing |"
    separator = "| --- | --- | --- | --- | --- | --- | --- |"
    rows = [
        "| Row {i} | {analysis} | {response} | Digital and retail | Supply pressure | Team | Quarter |".format(
            i=i,
            analysis="Demand evidence remains mixed across priority segments " * 2,
            response="Protect margin while testing focused growth actions " * 2,
        )
        for i in range(1, 81)
    ]
    sources = [
        SourceSpec(
            source_id=f"source-{i}",
            title=f"Market evidence report {i}",
            organization="Evidence Institute",
            published_at="2025",
            url=f"https://example.com/research/{i}/a/very/long/path/to/the/source/document?edition=final",
        )
        for i in range(1, 16)
    ]
    return ReportRequest(
        title="Strategic Market Review",
        subtitle="Evidence-led choices and execution priorities",
        author="Strategy Team",
        executive_summary=(
            "The evidence points to a selective growth agenda. " * 35
            + "\n\nExecution should protect the core while funding measured experiments. " * 20
        ),
        executive_insights=[
            ExecutiveInsightSpec(
                claim="Prioritize the most defensible growth pools.",
                evidence="Customer evidence supports targeted investment rather than a broad expansion.",
                implication="Sequence initiatives against explicit milestones and stop conditions.",
            )
        ],
        sections=[
            SectionSpec(
                heading=headings,
                summary="A long section designed to exercise heading orphan protection and flowing content.",
                key_points=["Protect the base business.", "Fund evidence-led experiments."],
                body_markdown="\n".join([header, separator, *rows]),
                images=[
                    ImageSpec(asset_id="portrait", caption="Request caption should not override trusted metadata"),
                    ImageSpec(asset_id="landscape", caption="Request caption should not override trusted metadata"),
                ],
            )
        ],
        sources=sources,
        methodology="Sources were compared for scope, timing, and decision relevance.",
    )


def test_evidence_image_uses_contain(tmp_path: Path) -> None:
    path = tmp_path / "evidence.png"
    PILImage.new("RGB", (600, 1200), "white").save(path)
    image_component = EvidenceImage(path, max_width=400, max_height=300)

    image_component.wrap(400, 300)

    assert image_component.rendered_aspect_ratio == pytest.approx(image_component.source_aspect_ratio)
    assert image_component.drawWidth <= 400
    assert image_component.drawHeight <= 300


def test_evidence_image_wrap_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "evidence-idempotent.png"
    PILImage.new("RGB", (1000, 500), "white").save(path)
    image_component = EvidenceImage(path, max_width=400, max_height=300)

    base_size = (image_component.drawWidth, image_component.drawHeight)
    image_component.wrap(400, 40)
    restored_size = image_component.wrap(400, 300)

    assert restored_size == pytest.approx(base_size)


def _image_draw_matrices(page, reader: PdfReader) -> list[tuple[float, ...]]:
    matrices: list[tuple[float, ...]] = []
    latest_matrix: tuple[float, ...] | None = None
    for operands, operator in ContentStream(page.get_contents(), reader).operations:
        if operator == b"cm":
            latest_matrix = tuple(float(value) for value in operands)
        elif operator == b"Do" and latest_matrix is not None:
            matrices.append(latest_matrix)
    return matrices


def _image_layout_payload(*, include_pair: bool, include_full: bool, include_odd: bool) -> ReportRequest:
    sections: list[SectionSpec] = []
    if include_pair:
        sections.append(
            SectionSpec(
                heading="Paired half evidence",
                images=[
                    ImageSpec(asset_id="half-one", caption="HALF ONE CAPTION", layout="half"),
                    ImageSpec(asset_id="half-two", caption="HALF TWO CAPTION", layout="half"),
                ],
            )
        )
    if include_full:
        sections.append(
            SectionSpec(
                heading="Full evidence",
                images=[ImageSpec(asset_id="full", caption="FULL CAPTION", layout="full")],
            )
        )
    if include_odd:
        sections.append(
            SectionSpec(
                heading="Odd half evidence",
                images=[ImageSpec(asset_id="odd", caption="ODD HALF CAPTION", layout="half")],
            )
        )
    return ReportRequest(title="Image layout matrix", sections=sections, disclaimer="")


def _resolved_layout_images(path: Path, payload: ReportRequest) -> dict[tuple[int, int], ResolvedImage]:
    return {
        (section_index, image_index): ResolvedImage(
            path=path,
            caption=image.caption,
            report_title="Trusted evidence report",
            publisher="Evidence Institute",
            year=2026,
            source_page=image_index + 1,
        )
        for section_index, section in enumerate(payload.sections)
        for image_index, image in enumerate(section.images)
    }


def test_two_half_images_are_paired_narrower_than_full_and_keep_captions(
    builder: StrategicReportBuilder,
    theme: ReportTheme,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "paired-layout.png"
    PILImage.new("RGB", (1200, 600), "#DDEDEA").save(image_path)
    payload = _image_layout_payload(include_pair=True, include_full=False, include_odd=False)
    full_payload = _image_layout_payload(include_pair=False, include_full=True, include_odd=False)
    output = tmp_path / "paired-layout.pdf"
    full_output = tmp_path / "full-layout.pdf"

    result = builder.build(payload, _resolved_layout_images(image_path, payload), {}, output)
    full_result = builder.build(
        full_payload,
        _resolved_layout_images(image_path, full_payload),
        {},
        full_output,
    )

    reader = PdfReader(str(output))
    paired_page = next(
        page
        for page in reader.pages
        if "HALF ONE CAPTION" in (page.extract_text() or "")
    )
    full_reader = PdfReader(str(full_output))
    full_page = next(
        page
        for page in full_reader.pages
        if "FULL CAPTION" in (page.extract_text() or "")
    )
    paired_text = paired_page.extract_text() or ""
    paired_matrices = _image_draw_matrices(paired_page, reader)
    full_matrices = _image_draw_matrices(full_page, full_reader)

    assert "HALF TWO CAPTION" in paired_text
    assert len(paired_matrices) == 2
    assert len(full_matrices) == 1
    half_widths = [matrix[0] for matrix in paired_matrices]
    assert all(width < theme.portrait_content_width * 0.55 for width in half_widths)
    assert full_matrices[0][0] > max(half_widths) * 1.8
    assert all(math.isclose(matrix[0] / matrix[3], 2.0, rel_tol=0.01) for matrix in paired_matrices)
    assert result.image_count == 2
    assert result.rendered_image_keys == ((0, 0), (0, 1))
    assert full_result.image_count == 1
    assert full_result.rendered_image_keys == ((0, 0),)


def test_odd_half_image_uses_half_width_container_and_keeps_caption(
    builder: StrategicReportBuilder,
    theme: ReportTheme,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "odd-half-layout.png"
    PILImage.new("RGB", (1200, 600), "#E7EEF2").save(image_path)
    payload = _image_layout_payload(include_pair=False, include_full=False, include_odd=True)
    output = tmp_path / "odd-half-layout.pdf"

    result = builder.build(payload, _resolved_layout_images(image_path, payload), {}, output)

    reader = PdfReader(str(output))
    evidence_page = next(
        page
        for page in reader.pages
        if "ODD HALF CAPTION" in (page.extract_text() or "")
    )
    matrices = _image_draw_matrices(evidence_page, reader)

    assert len(matrices) == 1
    assert matrices[0][0] < theme.portrait_content_width * 0.55
    assert math.isclose(matrices[0][0] / matrices[0][3], 2.0, rel_tol=0.01)
    assert result.image_count == 1
    assert result.rendered_image_keys == ((0, 0),)


def _adaptive_payload(
    body_markdown: str,
    *,
    methodology: str = "",
    sources: list[SourceSpec] | None = None,
    images: list[ImageSpec] | None = None,
) -> ReportRequest:
    return ReportRequest(
        title="Adaptive Report",
        author="Strategy Team",
        sections=[
            SectionSpec(
                heading="Section Marker",
                body_markdown=body_markdown,
                images=images or [],
            )
        ],
        methodology=methodology,
        sources=sources or [],
        disclaimer="",
    )


def _assert_no_markerless_pages(reader: PdfReader, markers: tuple[str, ...]) -> None:
    for page_index, page in enumerate(reader.pages):
        if page_index == 0:
            continue
        text = page.extract_text() or ""
        assert any(marker in text for marker in markers), (page_index + 1, text)


def test_very_tall_table_header_builds_and_remains_extractable(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    oversized_header = "H" * 5000
    body = f"|{oversized_header}|B|\n|---|---|\n|ROWVALUE|SECONDVALUE|"
    output = tmp_path / "very-tall-header.pdf"

    result = builder.build(_adaptive_payload(body), {}, {}, output)

    extracted = "".join(
        "".join((page.extract_text() or "").split())
        for page in PdfReader(str(output)).pages
    )
    assert result.page_count >= 3
    assert extracted.count("H") == len(oversized_header)
    assert "ROWVALUE" in extracted


def test_consecutive_wide_tables_do_not_create_spacer_only_pages(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    header = "|A|B|C|D|E|F|G|"
    separator = "|---|---|---|---|---|---|---|"
    body = "\n".join(
        [
            header,
            separator,
            "|TABLEONE|1|2|3|4|5|6|",
            "",
            header,
            separator,
            "|TABLETWO|1|2|3|4|5|6|",
        ]
    )
    output = tmp_path / "consecutive-wide.pdf"

    builder.build(_adaptive_payload(body), {}, {}, output)

    reader = PdfReader(str(output))
    _assert_no_markerless_pages(reader, ("Section Marker", "TABLEONE", "TABLETWO"))
    table_pages = [
        page
        for page in reader.pages
        if "TABLEONE" in (page.extract_text() or "") or "TABLETWO" in (page.extract_text() or "")
    ]
    assert table_pages
    assert all(float(page.mediabox.width) > float(page.mediabox.height) for page in table_pages)


def test_wide_table_transitions_to_methodology_and_sources_without_blank_pages(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    body = "\n".join(
        [
            "|A|B|C|D|E|F|G|",
            "|---|---|---|---|---|---|---|",
            "|WIDETABLE|1|2|3|4|5|6|",
        ]
    )
    sources = [
        SourceSpec(
            source_id="source-1",
            title="SOURCE TITLE MARKER",
            organization="Evidence Institute",
            published_at="2025",
        )
    ]
    output = tmp_path / "wide-to-backmatter.pdf"

    builder.build(
        _adaptive_payload(body, methodology="METHODOLOGY MARKER", sources=sources),
        {},
        {},
        output,
    )

    reader = PdfReader(str(output))
    _assert_no_markerless_pages(
        reader,
        ("Section Marker", "WIDETABLE", "METHODOLOGY MARKER", "SOURCE TITLE MARKER"),
    )


def test_markdown_heading_moves_to_same_landscape_page_as_wide_table(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    body = "\n".join(
        [
            "## ADAPTIVE HEADING MARKER",
            "|TABLE HEADER MARKER|B|C|D|E|F|G|",
            "|---|---|---|---|---|---|---|",
            "|row|1|2|3|4|5|6|",
        ]
    )
    output = tmp_path / "heading-wide.pdf"

    builder.build(_adaptive_payload(body), {}, {}, output)

    reader = PdfReader(str(output))
    matching_pages = []
    for page in reader.pages:
        normalized_text = "".join((page.extract_text() or "").split())
        if (
            "ADAPTIVEHEADINGMARKER" in normalized_text
            and "TABLEHEADERMARKER" in normalized_text
        ):
            matching_pages.append(page)
    assert len(matching_pages) == 1
    assert float(matching_pages[0].mediabox.width) > float(matching_pages[0].mediabox.height)


def test_very_long_markdown_heading_rewraps_for_landscape_without_blank_page(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    prefix = "LONG LANDSCAPE HEADING START "
    suffix = " LONG LANDSCAPE HEADING END"
    heading = prefix + ("L" * (1500 - len(prefix) - len(suffix))) + suffix
    assert len(heading) == 1500
    body = "\n".join(
        [
            f"## {heading}",
            "|TABLE HEADER MARKER|B|C|D|E|F|G|",
            "|---|---|---|---|---|---|---|",
            "|ROW MARKER|1|2|3|4|5|6|",
        ]
    )
    output = tmp_path / "very-long-heading-wide.pdf"

    builder.build(
        _adaptive_payload(body, methodology="PORTRAIT END MARKER"),
        {},
        {},
        output,
    )

    reader = PdfReader(str(output))
    landscape_pages = [
        page
        for page in reader.pages
        if float(page.mediabox.width) > float(page.mediabox.height)
    ]
    normalized_landscape_texts = [
        "".join((page.extract_text() or "").split()) for page in landscape_pages
    ]
    substantive_markers = (
        "LONGLANDSCAPEHEADINGSTART",
        "LONGLANDSCAPEHEADINGEND",
        "TABLEHEADERMARKER",
        "ROWMARKER",
    )

    assert landscape_pages
    assert all(
        any(marker in text for marker in substantive_markers)
        for text in normalized_landscape_texts
    )
    assert sum(
        "LONGLANDSCAPEHEADINGSTART" in text
        and "LONGLANDSCAPEHEADINGEND" in text
        and "TABLEHEADERMARKER" in text
        for text in normalized_landscape_texts
    ) == 1
    methodology_pages = [
        page
        for page in reader.pages
        if "PORTRAITENDMARKER" in "".join((page.extract_text() or "").split())
    ]
    assert len(methodology_pages) == 1
    assert float(methodology_pages[0].mediabox.width) < float(
        methodology_pages[0].mediabox.height
    )


def test_manifest_rejects_overlong_trusted_caption_metadata(tmp_path: Path) -> None:
    image_path = tmp_path / "manifest-image.png"
    PILImage.new("RGB", (20, 20), "white").save(image_path)
    base = {
        "asset_id": "asset-1",
        "path": image_path.name,
        "report_title": "Title",
        "publisher": "Publisher",
        "year": 2025,
        "source_page": 1,
        "caption": "Caption",
        "usage_scope": "internal-analysis",
    }
    limits = {"report_title": 300, "publisher": 200, "caption": 600}
    manifest_path = tmp_path / "manifest.json"

    for field, limit in limits.items():
        item = {**base, field: "L" * (limit + 1)}
        manifest_path.write_text(json.dumps({"assets": [item]}), encoding="utf-8")
        with pytest.raises(AssetManifestError, match=field):
            AssetRegistry.from_manifest(tmp_path, manifest_path)


def test_maximum_trusted_caption_metadata_stays_with_image(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "maximum-caption.png"
    PILImage.new("RGB", (1000, 600), "white").save(image_path)
    caption = "CAPTIONSTART" + ("C" * 578) + "CAPTIONEND"
    report_title = "TITLESTART" + ("T" * 282) + "TITLEEND"
    publisher = "PUBLISHERSTART" + ("P" * 174) + "PUBLISHEREND"
    assert (len(caption), len(report_title), len(publisher)) == (600, 300, 200)
    payload = _adaptive_payload(
        "Evidence body.",
        images=[ImageSpec(asset_id="maximum", caption="Fallback caption")],
    )
    resolved = ResolvedImage(
        image_path,
        caption,
        report_title,
        publisher,
        2025,
        9999,
    )
    output = tmp_path / "maximum-caption.pdf"

    result = builder.build(payload, {(0, 0): resolved}, {}, output)

    pages = ["".join((page.extract_text() or "").split()) for page in PdfReader(str(output)).pages]
    caption_pages = [
        text
        for text in pages
        if all(
            marker in text
            for marker in (
                "CAPTIONSTART",
                "CAPTIONEND",
                "TITLESTART",
                "TITLEEND",
                "PUBLISHERSTART",
                "PUBLISHEREND",
                "page9999",
            )
        )
    ]
    assert result.image_count == 1
    assert not result.warnings
    assert len(caption_pages) == 1


def test_caption_too_tall_skips_image_with_warning(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "oversized-caption.png"
    PILImage.new("RGB", (1000, 600), "white").save(image_path)
    payload = _adaptive_payload(
        "Evidence body.",
        images=[ImageSpec(asset_id="oversized", caption="Fallback caption")],
    )
    resolved = ResolvedImage(
        image_path,
        "TOO TALL CAPTION " * 1000,
        "Report title",
        "Publisher",
        2025,
        1,
    )
    output = tmp_path / "oversized-caption.pdf"

    result = builder.build(payload, {(0, 0): resolved}, {}, output)

    assert output.is_file()
    assert result.image_count == 0
    assert any("图注" in warning and "过长" in warning for warning in result.warnings)


def test_long_report_builds_without_layout_error(
    builder: StrategicReportBuilder,
    stress_payload: ReportRequest,
    resolved_images: dict[tuple[int, int], ResolvedImage],
    tmp_path: Path,
) -> None:
    output = tmp_path / "stress.pdf"

    result = builder.build(stress_payload, resolved_images, {}, output)

    assert result.page_count >= 8
    assert not any("LayoutError" in warning for warning in result.warnings)
    assert output.is_file()


def test_thirty_source_appendix_splits_across_pages(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    sources = [
        SourceSpec(
            source_id=f"source-{index:02d}",
            title=f"SOURCE MARKER {index:02d} " + ("long audited evidence title " * 8),
            organization="Acceptance Evidence Institute",
            published_at="2026-08-05",
            data_period=f"Acceptance cycle {index:02d}",
            url=(
                "https://research.example.com/verified/"
                f"{index:02d}/a/long/path/to/the/audited/source/document?edition=final"
            ),
        )
        for index in range(1, 31)
    ]
    payload = ReportRequest(
        title="Source appendix split report",
        sections=[SectionSpec(heading="Body", body_markdown="Acceptance body.")],
        sources=sources,
        disclaimer="",
    )
    output = tmp_path / "source-appendix.pdf"

    result = builder.build(payload, {}, {}, output)

    page_texts = [page.extract_text() or "" for page in PdfReader(str(output)).pages]
    source_pages = [text for text in page_texts if "SOURCE MARKER" in text]
    assert result.page_count >= 3
    assert len(source_pages) >= 2
    assert all(f"SOURCE MARKER {index:02d}" in "".join(page_texts) for index in range(1, 31))


def test_wide_table_uses_landscape_pages(
    builder: StrategicReportBuilder,
    stress_payload: ReportRequest,
    resolved_images: dict[tuple[int, int], ResolvedImage],
    tmp_path: Path,
) -> None:
    output = tmp_path / "landscape.pdf"
    builder.build(stress_payload, resolved_images, {}, output)

    reader = PdfReader(str(output))
    sizes = [(float(page.mediabox.width), float(page.mediabox.height)) for page in reader.pages]
    assert any(width > height for width, height in sizes)
    assert any(width < height for width, height in sizes)


def test_trusted_evidence_caption_and_metadata_are_extractable(
    builder: StrategicReportBuilder,
    stress_payload: ReportRequest,
    resolved_images: dict[tuple[int, int], ResolvedImage],
    tmp_path: Path,
) -> None:
    output = tmp_path / "metadata.pdf"
    result = builder.build(stress_payload, resolved_images, {}, output)

    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(str(output)).pages)
    assert result.image_count == 2
    assert "Trusted portrait evidence caption" in extracted
    assert "Trusted Source Report" in extracted
    assert "Evidence Institute" in extracted
    assert "page 11" in extracted.lower()


def test_chart_title_and_attribution_stay_together_without_duplicate_title(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    """A chart title belongs to the report block, with all attribution below it."""
    chart_path = tmp_path / "chart.png"
    PILImage.new("RGB", (1200, 600), "white").save(chart_path)
    chart = ChartSpec(
        type="bar",
        title="CHART TITLE MARKER",
        labels=["A", "B"],
        datasets=[DatasetSpec(label="规模", data=[1, 2])],
        unit="亿元",
        source="CHART SOURCE MARKER",
        note="CHART NOTE MARKER",
        source_ids=["chart-source"],
    )
    payload = ReportRequest(
        title="Chart layout report",
        author="Strategy Team",
        sections=[SectionSpec(heading="Chart section", charts=[chart])],
        sources=[
            SourceSpec(
                source_id="chart-source",
                title="Chart Source",
                organization="Evidence Institute",
                published_at="2025",
            )
        ],
        disclaimer="",
    )
    output = tmp_path / "chart-layout.pdf"

    builder.build(payload, {}, {(0, 0): chart_path}, output)

    chart_pages = [
        page.extract_text() or ""
        for page in PdfReader(str(output)).pages
        if "CHART TITLE MARKER" in (page.extract_text() or "")
    ]
    assert len(chart_pages) == 1
    chart_page = chart_pages[0]
    assert chart_page.count("CHART TITLE MARKER") == 1
    assert "单位：亿元" in chart_page
    assert "CHART SOURCE MARKER" in chart_page
    assert "CHART NOTE MARKER" in chart_page


def test_maximum_long_chart_labels_do_not_skip_valid_chart(
    builder: StrategicReportBuilder,
    tmp_path: Path,
) -> None:
    chart_path = tmp_path / "maximum-label-chart.png"
    PILImage.new("RGB", (1200, 600), "white").save(chart_path)
    labels = [
        f"LABEL-{index:02d}-" + ("Q" * 140)
        for index in range(30)
    ]
    chart = ChartSpec(
        type="bar",
        title="MAXIMUM LABEL CHART",
        labels=labels,
        datasets=[
            DatasetSpec(
                label="SERIES-LABEL-" + ("Z" * 137),
                data=[float(index) for index in range(30)],
            )
        ],
        unit="units",
        source="Maximum-label layout source",
        source_ids=["chart-source"],
    )
    payload = ReportRequest(
        title="Maximum label report",
        sections=[
            SectionSpec(
                heading="Chart section",
                charts=[chart],
                source_ids=["chart-source"],
            )
        ],
        sources=[
            SourceSpec(
                source_id="chart-source",
                title="Maximum label source",
                organization="Evidence Institute",
                published_at="2026",
            )
        ],
        disclaimer="",
    )
    output = tmp_path / "maximum-label-chart.pdf"

    result = builder.build(payload, {}, {(0, 0): chart_path}, output)
    extracted = "".join(
        page.extract_text() or "" for page in PdfReader(str(output)).pages
    )

    assert result.image_count == 1
    assert result.rendered_chart_keys == ((0, 0),)
    assert not any("MAXIMUM LABEL CHART" in warning for warning in result.warnings)
    assert "LABEL-00" in extracted
    assert "LABEL-29" in extracted
    assert "SERIES-L" in extracted
