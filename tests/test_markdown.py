from __future__ import annotations

import pytest
from reportlab.platypus import CondPageBreak, Paragraph, Table

from app.layout import ReportTheme
from app.markdown import (
    link_markup,
    markdown_to_flowables,
    normalize_source_title,
)


@pytest.fixture
def theme() -> ReportTheme:
    return ReportTheme(regular_font="Helvetica", bold_font="Helvetica-Bold")


def test_adjacent_lines_join_into_one_paragraph(theme: ReportTheme) -> None:
    items = markdown_to_flowables("First line\ncontinues the same thought.", theme)

    paragraphs = [item for item in items if isinstance(item, Paragraph)]
    assert len(paragraphs) == 1
    assert paragraphs[0].getPlainText() == "First line continues the same thought."


def test_heading_reserves_space_for_following_body(theme: ReportTheme) -> None:
    items = markdown_to_flowables("## A long strategic heading\n\nSupporting text.", theme)

    assert isinstance(items[0], CondPageBreak)
    assert isinstance(items[1], Paragraph)
    assert items[1].style.keepWithNext is True


def test_wrapped_heading_reserves_its_dynamic_height(theme: ReportTheme) -> None:
    short_items = markdown_to_flowables("# Short heading", theme)
    long_items = markdown_to_flowables("# " + ("Long strategic heading " * 12), theme)

    assert long_items[0].height > short_items[0].height


def test_extreme_heading_reserve_is_capped_and_can_split(theme: ReportTheme) -> None:
    items = markdown_to_flowables("# " + ("Very long heading " * 900), theme)

    assert items[0].height <= theme.portrait_content_height
    assert items[1].getKeepWithNext() is False


def test_markdown_heading_is_marked_for_adaptive_landscape_move(theme: ReportTheme) -> None:
    items = markdown_to_flowables("## Strategic table heading", theme)

    heading = next(item for item in items if isinstance(item, Paragraph))
    assert heading.adaptive_heading is True


def test_numbered_list_keeps_explicit_number(theme: ReportTheme) -> None:
    items = markdown_to_flowables("7. First action\n8) Second action", theme)

    paragraphs = [item for item in items if isinstance(item, Paragraph)]
    assert [item.bulletText for item in paragraphs] == ["7.", "8."]
    assert [item.getPlainText() for item in paragraphs] == ["First action", "Second action"]


def test_source_number_is_not_duplicated() -> None:
    assert normalize_source_title(1, "1. 中国家纺报告") == "1. 中国家纺报告"
    assert normalize_source_title(2, "中国家纺报告") == "2. 中国家纺报告"


def test_markdown_table_repeats_header_and_uses_paragraph_cells(theme: ReportTheme) -> None:
    items = markdown_to_flowables("|A|B|\n|---|---|\n|1|2|", theme)

    table = next(item for item in items if isinstance(item, Table))
    assert table.repeatRows == 1
    assert all(isinstance(cell, Paragraph) for row in table._cellvalues for cell in row)


def test_oversized_markdown_table_header_is_not_repeated(theme: ReportTheme) -> None:
    oversized_header = "H" * 5000
    items = markdown_to_flowables(
        f"|{oversized_header}|B|\n|---|---|\n|row-value|second-value|",
        theme,
    )

    table = next(item for item in items if isinstance(item, Table))
    assert table.repeatRows == 0


def test_wide_markdown_table_requests_landscape_template(theme: ReportTheme) -> None:
    items = markdown_to_flowables(
        "|A|B|C|D|E|F|G|\n|---|---|---|---|---|---|---|\n|1|2|3|4|5|6|7|",
        theme,
    )

    table = next(item for item in items if isinstance(item, Table))
    assert table.requires_landscape is True


def test_display_url_uses_breakable_label() -> None:
    url = "https://example.com/a/very/long/path?x=1&y=2"
    value = link_markup("来源页面", url)

    assert "来源页面" in value
    assert "href=" in value
    assert "&amp;" in value


def test_only_https_links_and_supported_markup_survive(theme: ReportTheme) -> None:
    items = markdown_to_flowables(
        "<script>alert(1)</script> **bold** `code` [unsafe](http://example.com)",
        theme,
    )

    paragraph = next(item for item in items if isinstance(item, Paragraph))
    assert "<script>" not in paragraph.text
    assert "&lt;script&gt;" in paragraph.text
    assert "<b>bold</b>" in paragraph.text
    assert "<font" in paragraph.text
    assert "http://example.com" in paragraph.getPlainText()
    assert "href=" not in paragraph.text
