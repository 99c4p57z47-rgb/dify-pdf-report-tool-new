"""Deterministic, embedded CJK font setup for generated PDF reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


REGULAR_FILENAME = "NotoSansCJK-Regular.ttc"
BOLD_FILENAME = "NotoSansCJK-Bold.ttc"
REGULAR_FONT_NAME = "ReportCJK"
BOLD_FONT_NAME = "ReportCJKBold"
PROBE_FILENAME = "font-probe.pdf"
PROBE_TEXT = "中文字体探针 2026"


class FontSetupError(RuntimeError):
    """Raised when the required embedded Chinese fonts are unavailable or invalid."""


@dataclass(frozen=True)
class FontRegistry:
    regular_name: str
    bold_name: str
    regular_path: Path
    bold_path: Path


def register_fonts(font_dir: Path) -> FontRegistry:
    """Register the two Noto CJK fonts packaged at *font_dir*.

    There is intentionally no platform discovery or CID-font fallback: the PDF
    must contain the deterministic font files shipped by the image.
    """
    regular_path = font_dir / REGULAR_FILENAME
    bold_path = font_dir / BOLD_FILENAME
    missing = [path.name for path in (regular_path, bold_path) if not path.is_file()]
    if missing:
        raise FontSetupError(f"缺少必需的中文字体：{', '.join(missing)}")

    try:
        pdfmetrics.registerFont(TTFont(REGULAR_FONT_NAME, str(regular_path), subfontIndex=0))
        pdfmetrics.registerFont(TTFont(BOLD_FONT_NAME, str(bold_path), subfontIndex=0))
    except Exception as exc:
        raise FontSetupError(f"中文字体注册失败：{exc}") from exc

    return FontRegistry(
        regular_name=REGULAR_FONT_NAME,
        bold_name=BOLD_FONT_NAME,
        regular_path=regular_path,
        bold_path=bold_path,
    )


def verify_font_probe(registry: FontRegistry, work_dir: Path) -> None:
    """Create and parse a CJK PDF probe, raising when it is not readable."""
    probe_path = work_dir / PROBE_FILENAME
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        document = canvas.Canvas(str(probe_path))
        document.setFont(registry.regular_name, 14)
        document.drawString(72, 720, PROBE_TEXT)
        document.save()

        reader = PdfReader(str(probe_path))
        if not reader.pages:
            raise ValueError("probe PDF has no pages")
        extracted = reader.pages[0].extract_text() or ""
        if PROBE_TEXT not in extracted:
            raise ValueError("probe Chinese text is not extractable")
    except Exception as exc:
        raise FontSetupError(f"中文字体探针失败：{exc}") from exc
