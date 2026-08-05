"""Strategic consulting PDF layout built entirely from Platypus Flowables."""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from PIL import Image as PILImage
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.assets import ResolvedImage
from app.fonts import FontRegistry
from app.models import ChartSpec, ExecutiveInsightSpec, ReportRequest, SourceSpec


ImageMap = Mapping[tuple[int, int], ResolvedImage]
ChartMap = Mapping[tuple[int, int], Path]
_CHART_ACCESSIBLE_LABEL_WIDTH = 8


def chart_accessible_labels(
    chart: ChartSpec,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the bounded label summary that is extractable below a chart."""
    def shorten(value: str) -> str:
        if len(value) <= _CHART_ACCESSIBLE_LABEL_WIDTH:
            return value
        return value[:_CHART_ACCESSIBLE_LABEL_WIDTH] + "…"

    return (
        tuple(shorten(label) for label in chart.labels),
        tuple(shorten(dataset.label) for dataset in chart.datasets if dataset.label),
    )


@dataclass(frozen=True)
class BuildResult:
    page_count: int
    image_count: int
    warnings: tuple[str, ...] = ()
    rendered_image_keys: tuple[tuple[int, int], ...] = ()
    rendered_chart_keys: tuple[tuple[int, int], ...] = ()


@dataclass
class _StoryState:
    flowables: list[Flowable] = field(default_factory=list)
    current_template: str = "portrait"
    pending_page_break: bool = False


@dataclass(frozen=True)
class ReportTheme:
    regular_font: str
    bold_font: str
    portrait_page_size: tuple[float, float] = A4
    landscape_page_size: tuple[float, float] = landscape(A4)
    left_margin: float = 18 * mm
    right_margin: float = 18 * mm
    top_margin: float = 20 * mm
    bottom_margin: float = 18 * mm
    body_size: float = 10.0
    caption_size: float = 8.2
    navy: str = "#18324A"
    graphite: str = "#263238"
    teal: str = "#2A8C82"
    pale: str = "#F4F7F8"
    amber: str = "#D8A444"
    muted: str = "#60717A"
    line: str = "#D8E1E5"
    minimum_readable_column_width: float = 28 * mm
    table_space_after: float = 4 * mm

    def __post_init__(self) -> None:
        if self.body_size < 9.5:
            raise ValueError("Body type must be at least 9.5 pt")
        if self.caption_size < 8:
            raise ValueError("Caption type must be at least 8 pt")
        if not self.regular_font or not self.bold_font:
            raise ValueError("ReportTheme requires registered regular and bold font names")

    @staticmethod
    def color(value: str) -> colors.Color:
        return colors.HexColor(value)

    @property
    def portrait_content_width(self) -> float:
        return self.portrait_page_size[0] - self.left_margin - self.right_margin

    @property
    def portrait_content_height(self) -> float:
        return self.portrait_page_size[1] - self.top_margin - self.bottom_margin

    @property
    def landscape_content_width(self) -> float:
        return self.landscape_page_size[0] - self.left_margin - self.right_margin

    @property
    def landscape_content_height(self) -> float:
        return self.landscape_page_size[1] - self.top_margin - self.bottom_margin

    @property
    def heading_reserve_height(self) -> float:
        return 25 + (self.body_size * 1.55 * 2) + 12

    def styles(self) -> dict[str, ParagraphStyle]:
        body_leading = self.body_size * 1.55
        body = ParagraphStyle(
            "StrategicBody",
            fontName=self.regular_font,
            fontSize=self.body_size,
            leading=body_leading,
            textColor=self.color(self.graphite),
            spaceAfter=2.6 * mm,
            wordWrap="CJK",
        )
        return {
            "cover_title": ParagraphStyle(
                "StrategicCoverTitle",
                parent=body,
                fontName=self.bold_font,
                fontSize=26,
                leading=34,
                textColor=self.color(self.navy),
                alignment=TA_LEFT,
                spaceAfter=7 * mm,
            ),
            "cover_subtitle": ParagraphStyle(
                "StrategicCoverSubtitle",
                parent=body,
                fontSize=13,
                leading=20,
                textColor=self.color(self.muted),
                spaceAfter=7 * mm,
            ),
            "h1": ParagraphStyle(
                "StrategicHeading1",
                parent=body,
                fontName=self.bold_font,
                fontSize=18,
                leading=25,
                textColor=self.color(self.navy),
                spaceBefore=5 * mm,
                spaceAfter=4 * mm,
                keepWithNext=True,
            ),
            "h2": ParagraphStyle(
                "StrategicHeading2",
                parent=body,
                fontName=self.bold_font,
                fontSize=13,
                leading=19,
                textColor=self.color(self.teal),
                spaceBefore=3 * mm,
                spaceAfter=2 * mm,
                keepWithNext=True,
            ),
            "body": body,
            "summary": ParagraphStyle(
                "StrategicSummary",
                parent=body,
                fontSize=max(10.2, self.body_size),
                leading=max(16, body_leading),
                backColor=self.color(self.pale),
                borderColor=self.color(self.line),
                borderWidth=0.6,
                borderPadding=8,
                spaceAfter=4 * mm,
            ),
            "bullet": ParagraphStyle(
                "StrategicBullet",
                parent=body,
                leftIndent=6 * mm,
                firstLineIndent=-3.5 * mm,
                bulletIndent=1.5 * mm,
                spaceAfter=1.5 * mm,
            ),
            "caption": ParagraphStyle(
                "StrategicCaption",
                parent=body,
                fontSize=self.caption_size,
                leading=max(11.5, self.caption_size * 1.4),
                textColor=self.color(self.muted),
                alignment=TA_CENTER,
                spaceAfter=4 * mm,
            ),
            "source": ParagraphStyle(
                "StrategicSource",
                parent=body,
                fontSize=max(9.5, self.body_size - 0.5),
                leading=max(13.5, self.body_size * 1.4),
                textColor=self.color(self.muted),
            ),
            "small": ParagraphStyle(
                "StrategicSmall",
                parent=body,
                fontSize=max(9.5, self.body_size - 0.5),
                leading=max(13.5, self.body_size * 1.4),
                textColor=self.color(self.muted),
            ),
            "table_header": ParagraphStyle(
                "StrategicTableHeader",
                parent=body,
                fontName=self.bold_font,
                fontSize=max(9.5, self.body_size - 0.5),
                leading=max(13, self.body_size * 1.35),
                textColor=colors.white,
            ),
            "table_body": ParagraphStyle(
                "StrategicTableBody",
                parent=body,
                fontSize=max(9.5, self.body_size - 0.5),
                leading=max(13, self.body_size * 1.35),
                spaceAfter=0,
            ),
            "card_label": ParagraphStyle(
                "StrategicCardLabel",
                parent=body,
                fontName=self.bold_font,
                fontSize=max(9.5, self.body_size - 0.5),
                leading=13,
                textColor=self.color(self.teal),
                spaceAfter=2 * mm,
            ),
            "card_body": ParagraphStyle(
                "StrategicCardBody",
                parent=body,
                fontSize=self.body_size,
                leading=body_leading,
                spaceAfter=0,
            ),
        }


class CoverPage(KeepTogether):
    def __init__(
        self,
        title: str,
        subtitle: str,
        author: str,
        generated_at: str,
        theme: ReportTheme,
    ) -> None:
        styles = theme.styles()
        label_style = styles["small"]
        metadata = [
            [Paragraph("生成机构", label_style), Paragraph(html.escape(author), styles["body"])],
            [Paragraph("生成时间", label_style), Paragraph(html.escape(generated_at), styles["body"])],
            [
                Paragraph("资料范围", label_style),
                Paragraph("内部知识库 + 已核验公开网络资料", styles["body"]),
            ],
        ]
        metadata_table = Table(metadata, colWidths=[28 * mm, 112 * mm], hAlign="LEFT")
        metadata_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.35, theme.color(theme.line)),
                ]
            )
        )
        badge = Table(
            [[Paragraph("行业研究 · 数据洞察 · 趋势研判", styles["table_header"]) ]],
            colWidths=[82 * mm],
            rowHeights=[10 * mm],
            hAlign="LEFT",
        )
        badge.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), theme.color(theme.navy)),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        content: list[Flowable] = [
            Spacer(1, 24 * mm),
            badge,
            Spacer(1, 11 * mm),
            Paragraph(html.escape(title), styles["cover_title"]),
        ]
        if subtitle:
            content.append(Paragraph(html.escape(subtitle), styles["cover_subtitle"]))
        content.extend([Spacer(1, 20 * mm), metadata_table])
        super().__init__(content)


class ExecutiveInsightCards(Table):
    def __init__(self, insights: Sequence[ExecutiveInsightSpec], theme: ReportTheme) -> None:
        styles = theme.styles()
        data: list[list[Flowable]] = [
            [
                Paragraph("战略判断", styles["table_header"]),
                Paragraph("证据基础", styles["table_header"]),
            ]
        ]
        for insight in insights:
            data.append(
                [
                    Paragraph(html.escape(insight.claim), styles["card_body"]),
                    Paragraph(html.escape(insight.evidence), styles["card_body"]),
                ]
            )
        super().__init__(
            data,
            colWidths=[theme.portrait_content_width * 0.38, theme.portrait_content_width * 0.62],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
        )
        self.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), theme.color(theme.navy)),
                    ("BACKGROUND", (0, 1), (0, -1), theme.color(theme.pale)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.4, theme.color(theme.line)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )


class SectionHeading(Paragraph):
    def __init__(self, text: str, theme: ReportTheme, *, level: int = 1) -> None:
        style = theme.styles()["h1" if level == 1 else "h2"]
        super().__init__(html.escape(text), style)


class KeyPointBox(Table):
    def __init__(self, points: Sequence[str], theme: ReportTheme) -> None:
        styles = theme.styles()
        data: list[list[Flowable]] = [[Paragraph("关键发现", styles["table_header"])]]
        data.extend(
            [Paragraph(f"• {html.escape(point)}", styles["body"])] for point in points
        )
        super().__init__(
            data,
            colWidths=[theme.portrait_content_width],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
        )
        self.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), theme.color(theme.teal)),
                    ("BACKGROUND", (0, 1), (-1, -1), theme.color(theme.pale)),
                    ("BOX", (0, 0), (-1, -1), 0.5, theme.color(theme.line)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        self.spaceAfter = 4 * mm


class EvidenceImage(Image):
    """An aspect-preserving image that only shrinks to fit available space."""

    def __init__(self, path: Path, *, max_width: float, max_height: float) -> None:
        with PILImage.open(path) as source:
            width, height = source.size
        if width <= 0 or height <= 0:
            raise ValueError(f"Image has invalid dimensions: {path}")
        self.source_aspect_ratio = width / height
        scale = min(max_width / width, max_height / height)
        super().__init__(
            str(path),
            width=width * scale,
            height=height * scale,
            kind="direct",
            hAlign="CENTER",
        )
        self._base_draw_width = self.drawWidth
        self._base_draw_height = self.drawHeight

    @property
    def rendered_aspect_ratio(self) -> float:
        return self.drawWidth / self.drawHeight

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        shrink = min(
            1.0,
            availWidth / self._base_draw_width,
            availHeight / self._base_draw_height,
        )
        self.drawWidth = self._base_draw_width * shrink
        self.drawHeight = self._base_draw_height * shrink
        return self.drawWidth, self.drawHeight


class SourceCaption(Paragraph):
    def __init__(self, text: str, theme: ReportTheme) -> None:
        super().__init__(html.escape(text), theme.styles()["caption"])


class RecommendationRoadmap(Table):
    def __init__(self, actions: Sequence[str], theme: ReportTheme) -> None:
        styles = theme.styles()
        data: list[list[Flowable]] = [
            [
                Paragraph("优先级", styles["table_header"]),
                Paragraph("行动路径", styles["table_header"]),
            ]
        ]
        for index, action in enumerate(actions, start=1):
            data.append(
                [
                    Paragraph(f"{index:02d}", styles["card_label"]),
                    Paragraph(html.escape(action), styles["body"]),
                ]
            )
        super().__init__(
            data,
            colWidths=[20 * mm, theme.portrait_content_width - 20 * mm],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
        )
        self.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), theme.color(theme.navy)),
                    ("BACKGROUND", (0, 1), (0, -1), theme.color("#FFF7E6")),
                    ("GRID", (0, 0), (-1, -1), 0.4, theme.color(theme.line)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )


def SourceList(sources: Sequence[SourceSpec], theme: ReportTheme) -> Table:
    """Return a splittable source table without subclass clone constraints."""
    from app.markdown import link_markup, normalize_source_title

    style = theme.styles()["source"]
    rows: list[list[Flowable]] = []
    for index, source in enumerate(sources, start=1):
        parts = [f"《{normalize_source_title(index, source.title)}》"]
        parts.append(source.organization)
        parts.append(source.published_at)
        if source.data_period:
            parts.append(f"数据期：{source.data_period}")
        if source.source_type:
            parts.append(source.source_type)
        markup = "｜".join(html.escape(part) for part in parts if part)
        if source.url:
            markup += "｜" + link_markup("来源页面", str(source.url), color=theme.teal)
        rows.append([Paragraph(markup, style)])
    table = Table(
        rows,
        colWidths=[theme.portrait_content_width],
        splitByRow=1,
        splitInRow=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, theme.color(theme.line)),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


class StrategicReportBuilder:
    def __init__(self, fonts: FontRegistry, theme: ReportTheme) -> None:
        pdfmetrics.getFont(fonts.regular_name)
        pdfmetrics.getFont(fonts.bold_name)
        self.fonts = fonts
        self.theme = replace(
            theme,
            regular_font=fonts.regular_name,
            bold_font=fonts.bold_name,
        )

    def _header_footer(self, canvas, document, title: str) -> None:
        canvas.saveState()
        page_number = canvas.getPageNumber()
        if page_number > 1:
            page_width, page_height = canvas._pagesize
            canvas.setStrokeColor(self.theme.color(self.theme.line))
            canvas.setLineWidth(0.5)
            canvas.line(
                self.theme.left_margin,
                page_height - 15 * mm,
                page_width - self.theme.right_margin,
                page_height - 15 * mm,
            )
            canvas.setFont(self.fonts.regular_name, 8)
            canvas.setFillColor(self.theme.color(self.theme.muted))
            canvas.drawString(
                self.theme.left_margin,
                page_height - 11 * mm,
                title[:52],
            )
            canvas.drawRightString(
                page_width - self.theme.right_margin,
                10 * mm,
                f"第 {page_number} 页",
            )
        canvas.restoreState()

    def _document(self, output_path: Path, payload: ReportRequest) -> BaseDocTemplate:
        theme = self.theme
        document = BaseDocTemplate(
            str(output_path),
            pagesize=theme.portrait_page_size,
            leftMargin=theme.left_margin,
            rightMargin=theme.right_margin,
            topMargin=theme.top_margin,
            bottomMargin=theme.bottom_margin,
            title=payload.title,
            author=payload.author,
            subject="家纺行业研究报告",
        )
        portrait_frame = Frame(
            theme.left_margin,
            theme.bottom_margin,
            theme.portrait_content_width,
            theme.portrait_content_height,
            id="portrait-frame",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        landscape_frame = Frame(
            theme.left_margin,
            theme.bottom_margin,
            theme.landscape_content_width,
            theme.landscape_content_height,
            id="landscape-frame",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        callback = lambda canvas, doc: self._header_footer(canvas, doc, payload.title)
        document.addPageTemplates(
            [
                PageTemplate(
                    id="portrait",
                    pagesize=theme.portrait_page_size,
                    frames=[portrait_frame],
                    onPage=callback,
                ),
                PageTemplate(
                    id="landscape",
                    pagesize=theme.landscape_page_size,
                    frames=[landscape_frame],
                    onPage=callback,
                ),
            ]
        )
        return document

    def _heading(self, text: str, *, level: int = 1) -> list[Flowable]:
        heading = SectionHeading(text, self.theme, level=level)
        _, heading_height = heading.wrap(
            self.theme.portrait_content_width,
            self.theme.portrait_content_height,
        )
        reserve_height = max(
            self.theme.heading_reserve_height,
            heading_height + (self.theme.body_size * 1.55 * 2) + heading.style.spaceAfter,
        )
        if heading_height > self.theme.portrait_content_height:
            heading.keepWithNext = False
        reserve_height = min(reserve_height, self.theme.portrait_content_height)
        return [
            CondPageBreak(reserve_height),
            heading,
        ]

    @staticmethod
    def _pull_trailing_heading(story: list[Flowable]) -> list[Flowable]:
        if not story or not (
            isinstance(story[-1], SectionHeading)
            or getattr(story[-1], "adaptive_heading", False)
        ):
            return []
        heading: list[Flowable] = [story.pop()]
        if story and isinstance(story[-1], CondPageBreak):
            heading.insert(0, story.pop())
        return heading

    @staticmethod
    def _is_adaptive_heading(item: Flowable) -> bool:
        return isinstance(item, SectionHeading) or getattr(item, "adaptive_heading", False)

    def _landscape_heading_group(self, heading: Flowable) -> list[Flowable]:
        _, heading_height = heading.wrap(
            self.theme.landscape_content_width,
            self.theme.landscape_content_height,
        )
        reserve_height = max(
            self.theme.heading_reserve_height,
            heading_height + (self.theme.body_size * 1.55 * 2) + heading.style.spaceAfter,
        )
        if heading_height > self.theme.landscape_content_height:
            heading.keepWithNext = False
        return [
            CondPageBreak(
                min(reserve_height, self.theme.landscape_content_height - 1)
            ),
            heading,
        ]

    @staticmethod
    def _request_page_break(state: _StoryState) -> None:
        state.pending_page_break = True

    @staticmethod
    def _begin_template(state: _StoryState, target: str) -> None:
        if state.current_template != target:
            state.flowables.extend([NextPageTemplate(target), PageBreak()])
            state.current_template = target
            state.pending_page_break = False
        elif state.pending_page_break:
            state.flowables.append(PageBreak())
            state.pending_page_break = False

    def _append_adaptive(self, state: _StoryState, items: Sequence[Flowable]) -> None:
        pending = list(items)
        index = 0
        while index < len(pending):
            item = pending[index]
            # CondPageBreak subclasses PageBreak in ReportLab, but it is an
            # in-frame space reservation rather than an explicit page break.
            if isinstance(item, PageBreak) and not isinstance(item, CondPageBreak):
                self._request_page_break(state)
                index += 1
                continue

            if (
                isinstance(item, CondPageBreak)
                and index + 2 < len(pending)
                and self._is_adaptive_heading(pending[index + 1])
                and getattr(pending[index + 2], "requires_landscape", False)
            ):
                self._begin_template(state, "landscape")
                state.flowables.extend(
                    [
                        *self._landscape_heading_group(pending[index + 1]),
                        pending[index + 2],
                    ]
                )
                index += 3
                continue

            # CondPageBreak also subclasses Spacer. Preserve it so headings
            # retain their orphan protection; only discard decorative gaps.
            if (
                isinstance(item, Spacer)
                and not isinstance(item, CondPageBreak)
                and (state.current_template == "landscape" or state.pending_page_break)
            ):
                index += 1
                continue

            if getattr(item, "requires_landscape", False):
                heading = self._pull_trailing_heading(state.flowables)
                self._begin_template(state, "landscape")
                if heading:
                    heading = self._landscape_heading_group(heading[-1])
                state.flowables.extend([*heading, item])
                index += 1
                continue

            self._begin_template(state, "portrait")
            state.flowables.append(item)
            index += 1

    def _caption_text(self, resolved: ResolvedImage, fallback_caption: str) -> str:
        caption = resolved.caption or fallback_caption
        metadata: list[str] = []
        if resolved.report_title:
            metadata.append(resolved.report_title)
        if resolved.publisher:
            metadata.append(resolved.publisher)
        if resolved.year:
            metadata.append(str(resolved.year))
        if resolved.source_page:
            metadata.append(f"原页码 page {resolved.source_page}")
        if metadata:
            return f"{caption}｜来源：{'｜'.join(metadata)}"
        return caption

    def _evidence_items(
        self,
        path: Path,
        caption_text: str,
        *,
        max_width: float | None = None,
    ) -> list[Flowable]:
        block_width = max_width or self.theme.portrait_content_width
        caption = SourceCaption(caption_text, self.theme)
        _, caption_height = caption.wrap(
            block_width,
            self.theme.portrait_content_height,
        )
        spacer_height = 2 * mm
        caption_block_height = (
            caption_height
            + caption.getSpaceBefore()
            + caption.getSpaceAfter()
            + spacer_height
        )
        if caption_block_height >= self.theme.portrait_content_height:
            raise ValueError("图注过长，无法与图片同页")
        max_height = min(
            118 * mm,
            self.theme.portrait_content_height - caption_block_height - 1,
        )
        if max_height <= 0:
            raise ValueError("图注过长，无法与图片同页")
        image = EvidenceImage(
            path,
            max_width=block_width,
            max_height=max_height,
        )
        return [image, Spacer(1, spacer_height), caption]

    def _evidence_block(
        self,
        path: Path,
        caption_text: str,
        *,
        max_width: float | None = None,
    ) -> KeepTogether:
        return KeepTogether(
            self._evidence_items(path, caption_text, max_width=max_width)
        )

    def _half_evidence_row(self, blocks: Sequence[Sequence[Flowable]]) -> Table:
        gap = 6 * mm
        column_width = (self.theme.portrait_content_width - gap) / 2
        cells: list[object] = [blocks[0], "", blocks[1] if len(blocks) > 1 else ""]
        table = Table(
            [cells],
            colWidths=[column_width, gap, column_width],
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return table

    def _chart_caption_text(self, chart: ChartSpec) -> str:
        details: list[str] = []
        category_labels, dataset_labels = chart_accessible_labels(chart)
        if category_labels:
            details.append(f"维度：{'、'.join(category_labels)}")
        if dataset_labels:
            details.append(f"系列：{'、'.join(dataset_labels)}")
        if chart.unit:
            details.append(f"单位：{chart.unit}")
        details.append(f"来源：{chart.source}")
        if chart.note:
            details.append(f"注：{chart.note}")
        return "｜".join(details)

    def _chart_block(self, path: Path, chart: ChartSpec) -> KeepTogether:
        title = Paragraph(html.escape(chart.title), self.theme.styles()["h2"])
        caption = SourceCaption(self._chart_caption_text(chart), self.theme)
        _, title_height = title.wrap(
            self.theme.portrait_content_width,
            self.theme.portrait_content_height,
        )
        _, caption_height = caption.wrap(
            self.theme.portrait_content_width,
            self.theme.portrait_content_height,
        )
        spacer_height = 2 * mm
        text_height = (
            title_height
            + title.getSpaceBefore()
            + title.getSpaceAfter()
            + caption_height
            + caption.getSpaceBefore()
            + caption.getSpaceAfter()
            + spacer_height
        )
        if text_height >= self.theme.portrait_content_height:
            raise ValueError("图表标题或图注过长，无法与图片同页")
        max_height = min(
            118 * mm,
            self.theme.portrait_content_height - text_height - 1,
        )
        if max_height <= 0:
            raise ValueError("图表标题或图注过长，无法与图片同页")
        image = EvidenceImage(
            path,
            max_width=self.theme.portrait_content_width,
            max_height=max_height,
        )
        return KeepTogether([title, image, Spacer(1, spacer_height), caption])

    def _contents_table(self, payload: ReportRequest) -> Table:
        styles = self.theme.styles()
        data: list[list[Flowable]] = [
            [
                Paragraph("章节", styles["table_header"]),
                Paragraph("主题", styles["table_header"]),
            ]
        ]
        for index, section in enumerate(payload.sections, start=1):
            data.append(
                [
                    Paragraph(str(index), styles["table_body"]),
                    Paragraph(html.escape(section.heading), styles["table_body"]),
                ]
            )
        table = Table(
            data,
            colWidths=[18 * mm, self.theme.portrait_content_width - 18 * mm],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign="LEFT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.theme.color(self.theme.navy)),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.theme.color(self.theme.pale)]),
                    ("GRID", (0, 0), (-1, -1), 0.35, self.theme.color(self.theme.line)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    def build(
        self,
        payload: ReportRequest,
        images: ImageMap,
        chart_paths: ChartMap,
        output_path: Path,
    ) -> BuildResult:
        from app.markdown import markdown_to_flowables

        output_path.parent.mkdir(parents=True, exist_ok=True)
        generated_at = payload.generated_at or datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M"
        )
        warnings: list[str] = []
        image_count = 0
        rendered_image_keys: list[tuple[int, int]] = []
        rendered_chart_keys: list[tuple[int, int]] = []
        story = _StoryState()
        self._append_adaptive(
            story,
            [
                CoverPage(
                    payload.title,
                    payload.subtitle,
                    payload.author,
                    generated_at,
                    self.theme,
                )
            ],
        )
        self._request_page_break(story)

        if payload.executive_summary:
            self._append_adaptive(
                story,
                [
                    *self._heading("执行摘要"),
                    *markdown_to_flowables(payload.executive_summary, self.theme),
                ],
            )

        if payload.executive_insights:
            self._append_adaptive(
                story,
                [
                    *self._heading("核心洞察"),
                    ExecutiveInsightCards(payload.executive_insights, self.theme),
                    *self._heading("行动路线", level=2),
                    RecommendationRoadmap(
                        [insight.implication for insight in payload.executive_insights],
                        self.theme,
                    ),
                ],
            )

        self._append_adaptive(
            story,
            [*self._heading("报告目录"), self._contents_table(payload)],
        )
        self._request_page_break(story)

        for section_index, section in enumerate(payload.sections):
            section_items: list[Flowable] = [
                *self._heading(f"{section_index + 1}. {section.heading}")
            ]
            if section.summary:
                section_items.append(
                    Paragraph(html.escape(section.summary), self.theme.styles()["summary"])
                )
            if section.key_points:
                section_items.append(KeyPointBox(section.key_points, self.theme))
            if section.body_markdown:
                section_items.extend(
                    markdown_to_flowables(section.body_markdown, self.theme),
                )
            self._append_adaptive(story, section_items)

            half_width = (self.theme.portrait_content_width - 6 * mm) / 2
            pending_half: list[tuple[tuple[int, int], list[Flowable]]] = []

            def flush_half_images() -> None:
                nonlocal image_count
                if not pending_half:
                    return
                self._append_adaptive(
                    story,
                    [self._half_evidence_row([block for _, block in pending_half])],
                )
                image_count += len(pending_half)
                rendered_image_keys.extend(key for key, _ in pending_half)
                pending_half.clear()

            for image_index, image_spec in enumerate(section.images):
                if image_spec.layout == "full":
                    flush_half_images()
                resolved = images.get((section_index, image_index))
                if resolved is None or resolved.path is None:
                    if resolved and resolved.warning:
                        warnings.append(
                            f"图片《{image_spec.caption}》未加入PDF：{resolved.warning}"
                        )
                    continue
                try:
                    caption_text = self._caption_text(resolved, image_spec.caption)
                    if image_spec.layout == "half":
                        pending_half.append(
                            (
                                (section_index, image_index),
                                self._evidence_items(
                                    resolved.path,
                                    caption_text,
                                    max_width=half_width,
                                ),
                            )
                        )
                        if len(pending_half) == 2:
                            flush_half_images()
                    else:
                        self._append_adaptive(
                            story,
                            [self._evidence_block(resolved.path, caption_text)],
                        )
                        image_count += 1
                        rendered_image_keys.append((section_index, image_index))
                except Exception as exc:
                    warnings.append(f"图片《{image_spec.caption}》未加入PDF：{exc}")

            flush_half_images()

            for chart_index, chart in enumerate(section.charts):
                chart_path = chart_paths.get((section_index, chart_index))
                if chart_path is None:
                    continue
                try:
                    self._append_adaptive(
                        story,
                        [self._chart_block(chart_path, chart)],
                    )
                    image_count += 1
                    rendered_chart_keys.append((section_index, chart_index))
                except Exception as exc:
                    warnings.append(f"图表《{chart.title}》未加入PDF：{exc}")

            if section_index + 1 < len(payload.sections):
                self._append_adaptive(story, [Spacer(1, 4 * mm)])

        if payload.methodology:
            self._request_page_break(story)
            self._append_adaptive(
                story,
                [
                    *self._heading("方法与口径"),
                    *markdown_to_flowables(payload.methodology, self.theme),
                ],
            )

        if payload.sources:
            self._request_page_break(story)
            self._append_adaptive(
                story,
                [*self._heading("参考资料"), SourceList(payload.sources, self.theme)],
            )

        if payload.disclaimer:
            self._append_adaptive(
                story,
                [
                    *self._heading("说明", level=2),
                    Paragraph(
                        html.escape(payload.disclaimer),
                        self.theme.styles()["small"],
                    ),
                ],
            )

        document = self._document(output_path, payload)
        document.build(story.flowables)
        page_count = len(PdfReader(str(output_path)).pages)
        return BuildResult(
            page_count,
            image_count,
            tuple(warnings),
            tuple(rendered_image_keys),
            tuple(rendered_chart_keys),
        )


__all__ = [
    "BuildResult",
    "ChartMap",
    "chart_accessible_labels",
    "CoverPage",
    "EvidenceImage",
    "ExecutiveInsightCards",
    "ImageMap",
    "KeyPointBox",
    "RecommendationRoadmap",
    "ReportTheme",
    "SectionHeading",
    "SourceCaption",
    "SourceList",
    "StrategicReportBuilder",
]
