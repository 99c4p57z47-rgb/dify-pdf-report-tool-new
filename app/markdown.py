"""A deliberately small, safe Markdown-to-Platypus converter."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from reportlab.platypus import CondPageBreak, Paragraph, Spacer, Table, TableStyle

if TYPE_CHECKING:
    from reportlab.platypus import Flowable

    from app.layout import ReportTheme


_TABLE_SEPARATOR = re.compile(r":?-{3,}:?")
_NUMBERED_ITEM = re.compile(r"^(\d+)[.)]\s+(.*)$")
_BULLET_ITEM = re.compile(r"^[-*]\s+(.*)$")
_SOURCE_NUMBER = re.compile(r"^\s*\d+[.)、]\s*")
_INLINE_TOKEN = re.compile(
    r"\[([^\]\n]+)\]\((https://[^)\s]+)\)"
    r"|\*\*([^*\n]+)\*\*"
    r"|`([^`\n]+)`"
)


def normalize_source_title(index: int, title: str) -> str:
    """Add a source number unless the supplied title already has one."""
    value = title.strip()
    if _SOURCE_NUMBER.match(value):
        return value
    return f"{index}. {value}"


def _breakable_label(label: str) -> str:
    escaped = html.escape(label.strip() or "来源页面")
    if len(label) <= 48 and not label.lower().startswith("https://"):
        return escaped
    for token in ("/", "?", "&amp;", "=", "-", "_"):
        escaped = escaped.replace(token, f"{token}&#8203;")
    return escaped


def link_markup(label: str, url: str, *, color: str = "#2A8C82") -> str:
    """Return ReportLab-safe link markup with a label that can wrap."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Only absolute HTTPS links are supported")
    safe_url = html.escape(url, quote=True)
    safe_color = html.escape(color, quote=True)
    return f'<link href="{safe_url}" color="{safe_color}">{_breakable_label(label)}</link>'


def inline_markup(text: str, theme: ReportTheme) -> str:
    """Escape arbitrary input and retain only bold, code and HTTPS links."""
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN.finditer(text.strip()):
        parts.append(html.escape(text[cursor : match.start()]))
        label, url, bold, code = match.groups()
        if url is not None:
            parts.append(link_markup(label, url, color=theme.teal))
        elif bold is not None:
            parts.append(f"<b>{html.escape(bold)}</b>")
        else:
            font_name = html.escape(theme.regular_font, quote=True)
            parts.append(
                f'<font name="{font_name}" color="{theme.graphite}">{html.escape(code or "")}</font>'
            )
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def visible_markdown_text(text: str) -> str:
    """Return user text emitted by the supported inline Markdown subset.

    Link labels are visible PDF text; HTTPS targets are annotation metadata and
    therefore must not become PDF-survival markers. The token grammar is shared
    with ``inline_markup`` so unsupported syntax remains literal in both paths.
    """
    parts: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN.finditer(text):
        parts.append(text[cursor : match.start()])
        label, url, bold, code = match.groups()
        if url is not None:
            parts.append(label or "")
        elif bold is not None:
            parts.append(bold)
        else:
            parts.append(code or "")
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    cells = split_table_row(line)
    return bool(cells) and all(_TABLE_SEPARATOR.fullmatch(cell) for cell in cells)


def _table_flowable(rows: list[list[str]], theme: ReportTheme) -> Table:
    styles = theme.styles()
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    table_data: list[list[Paragraph]] = []
    for row_index, row in enumerate(normalized):
        style = styles["table_header"] if row_index == 0 else styles["table_body"]
        table_data.append([Paragraph(inline_markup(cell, theme), style) for cell in row])

    requires_landscape = (
        column_count * theme.minimum_readable_column_width > theme.portrait_content_width
    )
    available_width = (
        theme.landscape_content_width if requires_landscape else theme.portrait_content_width
    )
    available_height = (
        theme.landscape_content_height if requires_landscape else theme.portrait_content_height
    )
    column_width = available_width / column_count
    header_height = max(
        cell.wrap(max(1, column_width - 10), available_height)[1]
        for cell in table_data[0]
    ) + 10
    repeat_rows = 1 if header_height <= available_height else 0
    table = Table(
        table_data,
        colWidths=[column_width] * column_count,
        repeatRows=repeat_rows,
        splitByRow=1,
        splitInRow=1,
        hAlign="LEFT",
    )
    table.requires_landscape = requires_landscape
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), theme.color(theme.navy)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, theme.color(theme.line)),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [theme.color("#FFFFFF"), theme.color(theme.pale)],
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def markdown_to_flowables(text: str, theme: ReportTheme) -> list[Flowable]:
    """Convert the supported Markdown subset into safe, splittable Flowables."""
    styles = theme.styles()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    flowables: list[Flowable] = []
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        content = " ".join(part.strip() for part in paragraph_buffer if part.strip())
        if content:
            flowables.append(Paragraph(inline_markup(content, theme), styles["body"]))
        paragraph_buffer.clear()

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue

        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and is_table_separator(lines[index + 1])
        ):
            flush_paragraph()
            rows = [split_table_row(stripped)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(split_table_row(lines[index]))
                index += 1
            flowables.extend([_table_flowable(rows, theme), Spacer(1, theme.table_space_after)])
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading = Paragraph(
                inline_markup(heading_match.group(2), theme),
                styles["h1" if level == 1 else "h2"],
            )
            _, heading_height = heading.wrap(
                theme.portrait_content_width,
                theme.portrait_content_height,
            )
            reserve_height = max(
                theme.heading_reserve_height,
                heading_height + (theme.body_size * 1.55 * 2) + heading.style.spaceAfter,
            )
            heading.adaptive_heading = True
            if heading_height > theme.portrait_content_height:
                heading.keepWithNext = False
            reserve_height = min(reserve_height, theme.portrait_content_height)
            flowables.extend([CondPageBreak(reserve_height), heading])
            index += 1
            continue

        bullet_match = _BULLET_ITEM.match(stripped)
        numbered_match = _NUMBERED_ITEM.match(stripped)
        if bullet_match or numbered_match:
            flush_paragraph()
            if numbered_match:
                bullet_text = f"{numbered_match.group(1)}."
                content = numbered_match.group(2)
            else:
                bullet_text = "•"
                content = bullet_match.group(1) if bullet_match else ""
            flowables.append(
                Paragraph(
                    inline_markup(content, theme),
                    styles["bullet"],
                    bulletText=bullet_text,
                )
            )
            index += 1
            continue

        paragraph_buffer.append(stripped)
        index += 1

    flush_paragraph()
    return flowables


__all__ = [
    "inline_markup",
    "is_table_separator",
    "link_markup",
    "markdown_to_flowables",
    "normalize_source_title",
    "split_table_row",
    "visible_markdown_text",
]
