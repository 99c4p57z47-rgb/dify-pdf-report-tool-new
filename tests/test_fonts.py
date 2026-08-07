from pathlib import Path

import pytest

from app.fonts import FontSetupError, register_fonts, verify_font_probe


def test_missing_fonts_fail_instead_of_fallback(tmp_path: Path):
    """Removing the packaged fonts must stop PDF generation, not substitute STSong."""
    with pytest.raises(FontSetupError, match="中文字体"):
        register_fonts(tmp_path)


def test_registry_never_returns_stsong(font_dir: Path):
    """The registry must expose the embedded Noto fonts for both weights."""
    registry = register_fonts(font_dir)

    assert registry.regular_name != "STSong-Light"
    assert registry.bold_name != "STSong-Light"


def test_probe_writes_pdf_with_extractable_chinese(font_dir: Path, tmp_path: Path):
    """The packaged font must create a PDF whose Chinese probe text survives pypdf."""
    registry = register_fonts(font_dir)

    verify_font_probe(registry, tmp_path)

    probe = tmp_path / "font-probe.pdf"
    assert probe.is_file()
    from pypdf import PdfReader

    assert "中文字体探针 2026" in (PdfReader(str(probe)).pages[0].extract_text() or "")
