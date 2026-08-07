"""Deployment regression tests for ReportLab-compatible CJK fonts."""

from pathlib import Path


def test_docker_image_uses_cjk_font_with_truetype_outlines():
    """The Railway image must not feed Noto CFF collections to TTFont."""
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "fonts-wqy-microhei" in dockerfile
    assert "wqy-microhei.ttc" in dockerfile
    assert "cp /usr/share/fonts/opentype/noto/NotoSansCJK" not in dockerfile
