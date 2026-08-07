"""Build a safe, presentation-only view model for the HTML PDF renderer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import html
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


_INLINE = re.compile(
    r"\[([^\]\n]+)\]\((https://[^)\s]+)\)"
    r"|\*\*([^*\n]+)\*\*"
    r"|`([^`\n]+)`"
)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class FigureView:
    uri: str
    caption: str
    alt: str
    fit: str
    layout: str
    source_line: str


@dataclass(frozen=True)
class ChartView:
    uri: str
    title: str
    unit: str
    source: str
    note: str
    category_labels: tuple[str, ...]
    dataset_labels: tuple[str, ...]


@dataclass(frozen=True)
class ExecutiveInsightView:
    index: int
    claim: str
    evidence: str
    implication: str


@dataclass(frozen=True)
class SectionView:
    index: int
    heading: str
    summary: str
    body_html: str
    key_points: tuple[str, ...]
    figures: tuple[FigureView, ...]
    charts: tuple[ChartView, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceView:
    index: int
    source_id: str
    title: str
    organization: str
    published_at: str
    data_period: str
    source_type: str
    url: str


@dataclass(frozen=True)
class ReportHtmlView:
    title: str
    short_title: str
    subtitle: str
    author: str
    generated_at: str
    executive_summary_html: str
    executive_insights: tuple[ExecutiveInsightView, ...]
    sections: tuple[SectionView, ...]
    sources: tuple[SourceView, ...]
    methodology_html: str
    disclaimer_html: str
    cover_uri: str
    texture_uri: str
    warnings: tuple[str, ...]
    rendered_image_keys: tuple[tuple[int, int], ...]
    rendered_chart_keys: tuple[tuple[int, int], ...]

    @property
    def image_count(self) -> int:
        return len(self.rendered_image_keys) + len(self.rendered_chart_keys)


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _https_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    return text if parsed.scheme == "https" and parsed.netloc else ""


def _inline_html(text: str) -> str:
    value = str(text or "")
    parts: list[str] = []
    cursor = 0
    for match in _INLINE.finditer(value):
        parts.append(html.escape(value[cursor : match.start()]))
        label, url, bold, code = match.groups()
        if url is not None:
            safe_url = _https_url(url)
            if safe_url:
                parts.append(
                    f'<a href="{html.escape(safe_url, quote=True)}">'
                    f"{html.escape(label or safe_url)}</a>"
                )
            else:
                parts.append(html.escape(label or ""))
        elif bold is not None:
            parts.append(f"<strong>{html.escape(bold)}</strong>")
        else:
            parts.append(f"<code>{html.escape(code or '')}</code>")
        cursor = match.end()
    parts.append(html.escape(value[cursor:]))
    return "".join(parts)


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_to_html(text: str) -> str:
    """Render a bounded Markdown subset without allowing arbitrary HTML."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    paragraphs: list[str] = []

    def flush_paragraph() -> None:
        content = " ".join(part.strip() for part in paragraphs if part.strip())
        if content:
            output.append(f"<p>{_inline_html(content)}</p>")
        paragraphs.clear()

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            flush_paragraph()
            index += 1
            continue
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and lines[index + 1].strip().startswith("|")
        ):
            separator = _split_table_row(lines[index + 1])
            if separator and all(_TABLE_SEPARATOR.fullmatch(cell) for cell in separator):
                flush_paragraph()
                rows = [_split_table_row(line)]
                index += 2
                while index < len(lines) and lines[index].strip().startswith("|"):
                    rows.append(_split_table_row(lines[index]))
                    index += 1
                width = max(len(row) for row in rows)
                normalized = [row + [""] * (width - len(row)) for row in rows]
                head = "".join(f"<th>{_inline_html(cell)}</th>" for cell in normalized[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{_inline_html(cell)}</td>" for cell in row) + "</tr>"
                    for row in normalized[1:]
                )
                output.append(
                    '<div class="table-wrap"><table><thead><tr>'
                    + head
                    + "</tr></thead><tbody>"
                    + body
                    + "</tbody></table></div>"
                )
                continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            level = min(4, len(heading.group(1)) + 2)
            output.append(f"<h{level}>{_inline_html(heading.group(2))}</h{level}>")
            index += 1
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if bullet or numbered:
            flush_paragraph()
            ordered = numbered is not None
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                current = lines[index].strip()
                match = (
                    re.match(r"^\d+[.)]\s+(.+)$", current)
                    if ordered
                    else re.match(r"^[-*]\s+(.+)$", current)
                )
                if not match:
                    break
                items.append(f"<li>{_inline_html(match.group(1))}</li>")
                index += 1
            output.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        paragraphs.append(line)
        index += 1
    flush_paragraph()
    return "\n".join(output)


def _source_line(image: Any) -> str:
    values = [
        _value(image, "report_title", ""),
        _value(image, "publisher", ""),
        str(_value(image, "year", "") or ""),
    ]
    page = _value(image, "source_page", None)
    if page:
        values.append(f"第{page}页")
    return "｜".join(str(value).strip() for value in values if str(value).strip())


def _short_chart_label(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= 8 else text[:8] + "…"


def build_report_view(
    payload: Any,
    image_paths: Mapping[tuple[int, int], Any],
    chart_paths: Mapping[tuple[int, int], Path],
    work_dir: Path,
    *,
    template_root: Path | None = None,
) -> ReportHtmlView:
    warnings: list[str] = []
    rendered_image_keys: list[tuple[int, int]] = []
    rendered_chart_keys: list[tuple[int, int]] = []
    section_views: list[SectionView] = []
    asset_root = Path(os.getenv("PDF_ASSET_DIR", "./assets")).resolve()
    cover_background = asset_root / "backgrounds" / "home_textile_cover_v1.png"
    cover_uri = cover_background.as_uri() if cover_background.is_file() else ""

    for section_index, section in enumerate(_value(payload, "sections", ())):
        figures: list[FigureView] = []
        for image_index, image_spec in enumerate(_value(section, "images", ())):
            key = (section_index, image_index)
            resolved = image_paths.get(key)
            path = _value(resolved, "path", None) if resolved is not None else None
            if path is None or not Path(path).is_file():
                warning = _value(resolved, "warning", "") if resolved is not None else ""
                warnings.append(
                    warning or f"章节《{_value(section, 'heading')}》图片{image_index + 1}未加入PDF"
                )
                continue
            uri = Path(path).resolve().as_uri()
            if not cover_uri:
                cover_uri = uri
            figures.append(
                FigureView(
                    uri=uri,
                    caption=str(_value(resolved, "caption", "") or _value(image_spec, "caption", "")),
                    alt=str(_value(image_spec, "alt", "") or _value(resolved, "caption", "图片")),
                    fit=str(_value(image_spec, "fit", "contain")),
                    layout=str(_value(image_spec, "layout", "full")),
                    source_line=_source_line(resolved),
                )
            )
            rendered_image_keys.append(key)

        charts: list[ChartView] = []
        for chart_index, chart_spec in enumerate(_value(section, "charts", ())):
            key = (section_index, chart_index)
            path = chart_paths.get(key)
            if path is None or not Path(path).is_file():
                warnings.append(f"章节《{_value(section, 'heading')}》图表{chart_index + 1}未加入PDF")
                continue
            charts.append(
                ChartView(
                    uri=Path(path).resolve().as_uri(),
                    title=str(_value(chart_spec, "title", "")),
                    unit=str(_value(chart_spec, "unit", "")),
                    source=str(_value(chart_spec, "source", "")),
                    note=str(_value(chart_spec, "note", "")),
                    category_labels=tuple(
                        _short_chart_label(item)
                        for item in _value(chart_spec, "labels", ())
                    ),
                    dataset_labels=tuple(
                        _short_chart_label(_value(item, "label", ""))
                        for item in _value(chart_spec, "datasets", ())
                        if _value(item, "label", "")
                    ),
                )
            )
            rendered_chart_keys.append(key)

        section_views.append(
            SectionView(
                index=section_index + 1,
                heading=str(_value(section, "heading", "")),
                summary=str(_value(section, "summary", "")),
                body_html=markdown_to_html(str(_value(section, "body_markdown", ""))),
                key_points=tuple(str(item) for item in _value(section, "key_points", ())),
                figures=tuple(figures),
                charts=tuple(charts),
                source_ids=tuple(str(item) for item in _value(section, "source_ids", ())),
            )
        )

    sources = tuple(
        SourceView(
            index=index,
            source_id=str(_value(source, "source_id", "")),
            title=str(_value(source, "title", "")),
            organization=str(_value(source, "organization", "")),
            published_at=str(_value(source, "published_at", "")),
            data_period=str(_value(source, "data_period", "")),
            source_type=str(_value(source, "source_type", "")),
            url=_https_url(_value(source, "url", "")),
        )
        for index, source in enumerate(_value(payload, "sources", ()), start=1)
    )

    root = template_root or Path(__file__).resolve().parent / "templates"
    texture = root / "static" / "texture.svg"
    generated_at = str(_value(payload, "generated_at", "")).strip() or date.today().isoformat()
    title = str(_value(payload, "title", "行业研究报告"))
    executive_insights = tuple(
        ExecutiveInsightView(
            index=index,
            claim=str(_value(item, "claim", "")),
            evidence=str(_value(item, "evidence", "")),
            implication=str(_value(item, "implication", "")),
        )
        for index, item in enumerate(
            _value(payload, "executive_insights", ()),
            start=1,
        )
    )
    return ReportHtmlView(
        title=title,
        short_title=title[:28],
        subtitle=str(_value(payload, "subtitle", "")),
        author=str(_value(payload, "author", "")),
        generated_at=generated_at,
        executive_summary_html=markdown_to_html(str(_value(payload, "executive_summary", ""))),
        executive_insights=executive_insights,
        sections=tuple(section_views),
        sources=sources,
        methodology_html=markdown_to_html(str(_value(payload, "methodology", ""))),
        disclaimer_html=markdown_to_html(str(_value(payload, "disclaimer", ""))),
        cover_uri=cover_uri,
        texture_uri=texture.resolve().as_uri() if texture.is_file() else "",
        warnings=tuple(warnings),
        rendered_image_keys=tuple(rendered_image_keys),
        rendered_chart_keys=tuple(rendered_chart_keys),
    )


def render_report_html(view: ReportHtmlView, template_root: Path | None = None) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:  # pragma: no cover - exercised by deployment health checks
        raise RuntimeError("Jinja2 is required for HTML PDF rendering") from exc

    root = template_root or Path(__file__).resolve().parent / "templates"
    environment = Environment(
        loader=FileSystemLoader(str(root)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    css = (root / "static" / "report.css").read_text(encoding="utf-8")
    font_uri = Path(os.getenv("CJK_FONT_DIR", "/app/fonts")).resolve().as_uri()
    css = css.replace("file:///app/fonts", font_uri)
    return environment.get_template("report.html").render(report=view, report_css=css)


__all__ = [
    "ChartView",
    "FigureView",
    "ExecutiveInsightView",
    "ReportHtmlView",
    "SectionView",
    "SourceView",
    "build_report_view",
    "markdown_to_html",
    "render_report_html",
]
