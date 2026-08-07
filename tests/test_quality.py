from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image as PILImage
from pypdf import PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app.quality import inspect_pdf
from scripts.render_pdf_pages import PdfRenderError, render_pages


def _write_text_pdf(path: Path, text: str) -> Path:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    document = canvas.Canvas(str(path))
    document.setTitle("PDF 质量检查测试")
    document.setAuthor("Codex")
    document.setFont("STSong-Light", 12)
    document.drawString(72, 720, text)
    document.save()
    return path


@pytest.fixture
def valid_pdf(tmp_path: Path) -> Path:
    return _write_text_pdf(
        tmp_path / "valid.pdf",
        "这是可提取的中文文本，用于验证生成报告的结构完整性和页面内容。",
    )


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "空白质量检查测试"})
    with path.open("wb") as output:
        writer.write(output)
    return path


def test_valid_pdf_passes_structure_check(valid_pdf: Path):
    summary = inspect_pdf(valid_pdf, expected_images=0)

    assert summary.status == "passed"
    assert summary.page_count > 0
    assert summary.errors == []


def test_malformed_pdf_is_fatal(tmp_path: Path):
    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"not a PDF")

    summary = inspect_pdf(malformed, expected_images=0)

    assert summary.status == "failed"
    assert any("无法读取" in error for error in summary.errors)


def test_missing_chinese_text_is_fatal(blank_pdf: Path):
    summary = inspect_pdf(
        blank_pdf,
        expected_images=0,
        required_chinese_markers=("市场概览",),
    )

    assert summary.status == "failed"
    assert any("市场概览" in error for error in summary.errors)


def test_english_pdf_does_not_require_chinese_text(tmp_path: Path):
    path = tmp_path / "english.pdf"
    document = canvas.Canvas(str(path))
    document.setTitle("English report")
    document.setFont("Helvetica", 12)
    document.drawString(
        72,
        720,
        "This English-only report contains enough extractable text for inspection.",
    )
    document.save()

    summary = inspect_pdf(path, expected_images=0)

    assert summary.status == "passed"
    assert summary.errors == []


def test_fixed_template_chinese_cannot_satisfy_missing_payload_marker(
    tmp_path: Path,
):
    path = _write_text_pdf(
        tmp_path / "template-only.pdf",
        "行业研究数据洞察趋势研判，这些固定模板文字不属于用户的标题。",
    )

    summary = inspect_pdf(
        path,
        expected_images=0,
        required_chinese_markers=("客户专属市场",),
    )

    assert summary.status == "failed"
    assert any("客户专属市场" in error for error in summary.errors)


def test_duplicate_field_markers_require_duplicate_pdf_occurrences(tmp_path: Path):
    path = _write_text_pdf(
        tmp_path / "one-occurrence.pdf",
        "重复字段中文只在输出中出现一次，其他文字用于避免稀疏页告警。",
    )

    summary = inspect_pdf(
        path,
        expected_images=0,
        required_chinese_markers=("重复字段中文", "重复字段中文"),
    )

    assert summary.status == "failed"
    assert any("至少 2 次" in error for error in summary.errors)


def test_zero_size_page_is_fatal(tmp_path: Path):
    path = tmp_path / "zero-size.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=0, height=792)
    with path.open("wb") as output:
        writer.write(output)

    summary = inspect_pdf(path, expected_images=0)

    assert summary.status == "failed"
    assert any("尺寸为零" in error for error in summary.errors)


def test_zero_page_pdf_is_fatal(tmp_path: Path):
    path = tmp_path / "zero-pages.pdf"
    writer = PdfWriter()
    writer.add_metadata({"/Title": "零页质量检查测试"})
    with path.open("wb") as output:
        writer.write(output)

    summary = inspect_pdf(path, expected_images=0)

    assert summary.status == "failed"
    assert "PDF 页数为零" in summary.errors


def test_missing_optional_images_is_a_warning(valid_pdf: Path):
    summary = inspect_pdf(valid_pdf, expected_images=2)

    assert summary.status == "passed_with_warnings"
    assert any("图片" in warning for warning in summary.warnings)


def test_repeated_image_draws_are_counted_separately(tmp_path: Path):
    image_path = tmp_path / "reused.png"
    PILImage.new("RGB", (24, 24), (30, 120, 180)).save(image_path)
    pdf_path = tmp_path / "repeated-image.pdf"
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    document = canvas.Canvas(str(pdf_path))
    document.setTitle("重复图片测试")
    document.setFont("STSong-Light", 12)
    document.drawString(72, 720, "重复图片测试包含足够的可提取中文文本。")
    document.drawImage(str(image_path), 72, 620, width=48, height=48)
    document.drawImage(str(image_path), 150, 620, width=48, height=48)
    document.save()

    summary = inspect_pdf(
        pdf_path,
        expected_images=2,
        required_chinese_markers=("重复图片测试",),
    )

    assert summary.status == "passed"
    assert summary.image_count == 2
    assert not any("少于预期" in warning for warning in summary.warnings)


def test_suspiciously_sparse_page_is_a_warning(tmp_path: Path):
    sparse_pdf = _write_text_pdf(tmp_path / "sparse.pdf", "中")

    summary = inspect_pdf(sparse_pdf, expected_images=0)

    assert summary.errors == []
    assert summary.status == "passed_with_warnings"
    assert any("内容稀疏" in warning for warning in summary.warnings)


def test_render_pages_removes_stale_pages_before_invoking_poppler(
    monkeypatch: pytest.MonkeyPatch,
    valid_pdf: Path,
    tmp_path: Path,
):
    calls: list[tuple[list[str], dict[str, object]]] = []
    output_dir = tmp_path / "rendered"
    output_dir.mkdir()
    (output_dir / "page-2.png").write_bytes(b"stale")
    (output_dir / "page-3.png").write_bytes(b"stale")

    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, kwargs))
        prefix = Path(arguments[-1])
        assert list(prefix.parent.glob("page-*.png")) == []
        prefix.with_name(f"{prefix.name}-1.png").write_bytes(b"png")
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    pages = render_pages(valid_pdf, output_dir, dpi=144)

    assert pages == [output_dir / "page-1.png"]
    assert not (output_dir / "page-2.png").exists()
    assert not (output_dir / "page-3.png").exists()
    arguments, kwargs = calls[0]
    assert arguments == [
        "pdftoppm",
        "-png",
        "-r",
        "144",
        str(valid_pdf),
        str(output_dir / "page"),
    ]
    assert kwargs == {
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "shell": False,
        "check": False,
    }


def test_render_pages_rejects_non_contiguous_output(
    monkeypatch: pytest.MonkeyPatch,
    valid_pdf: Path,
    tmp_path: Path,
):
    def fake_run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        prefix = Path(arguments[-1])
        prefix.with_name(f"{prefix.name}-1.png").write_bytes(b"png")
        prefix.with_name(f"{prefix.name}-3.png").write_bytes(b"png")
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(PdfRenderError, match="连续"):
        render_pages(valid_pdf, tmp_path / "rendered")


@pytest.mark.parametrize("diagnostic", ["Unknown font tag", "No font in show"])
def test_render_pages_treats_poppler_font_diagnostics_as_fatal(
    monkeypatch: pytest.MonkeyPatch,
    valid_pdf: Path,
    tmp_path: Path,
    diagnostic: str,
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="", stderr=f"Syntax Warning: {diagnostic}"
        ),
    )

    with pytest.raises(PdfRenderError, match=diagnostic):
        render_pages(valid_pdf, tmp_path / "rendered")


def test_render_pages_treats_timeout_as_fatal(
    monkeypatch: pytest.MonkeyPatch,
    valid_pdf: Path,
    tmp_path: Path,
):
    def timeout(*args: object, **kwargs: object):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=60, stderr="stalled")

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(PdfRenderError, match="60"):
        render_pages(valid_pdf, tmp_path / "rendered")


def test_render_pages_treats_nonzero_exit_as_fatal(
    monkeypatch: pytest.MonkeyPatch,
    valid_pdf: Path,
    tmp_path: Path,
):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 7, stdout="", stderr="broken xref"
        ),
    )

    with pytest.raises(PdfRenderError, match="broken xref"):
        render_pages(valid_pdf, tmp_path / "rendered")


def test_render_pages_reports_missing_pdftoppm_exactly(
    monkeypatch: pytest.MonkeyPatch,
    valid_pdf: Path,
    tmp_path: Path,
):
    def missing(*args: object, **kwargs: object):
        raise FileNotFoundError("pdftoppm")

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(PdfRenderError, match="pdftoppm"):
        render_pages(valid_pdf, tmp_path / "rendered")
