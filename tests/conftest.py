"""Shared pytest configuration for the PDF report tool."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def font_dir() -> Path:
    """Return an explicitly supplied local Noto CJK fixture, or skip."""
    configured = os.getenv("CJK_TEST_FONT_DIR", "").strip()
    if not configured:
        pytest.skip("CJK_TEST_FONT_DIR is required for the local Noto CJK font fixture")
    path = Path(configured)
    if not (
        (path / "NotoSansCJK-Regular.ttc").is_file()
        and (path / "NotoSansCJK-Bold.ttc").is_file()
    ):
        pytest.skip("CJK_TEST_FONT_DIR must contain NotoSansCJK-Regular.ttc and NotoSansCJK-Bold.ttc")
    return path
