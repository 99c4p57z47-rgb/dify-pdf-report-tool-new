"""Regression coverage for font-readiness failures at the service boundary."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import main
from app.models import ReportRequest


@pytest.fixture
def restore_font_setup_state():
    """Keep this module's startup-failure state from leaking to other tests."""
    missing = object()
    previous_registry = getattr(main.app.state, "font_registry", missing)
    previous_error = getattr(main.app.state, "font_error", missing)
    try:
        yield
    finally:
        if previous_registry is missing:
            main.app.state._state.pop("font_registry", None)
        else:
            main.app.state.font_registry = previous_registry
        if previous_error is missing:
            main.app.state._state.pop("font_error", None)
        else:
            main.app.state.font_error = previous_error


def _minimal_payload() -> ReportRequest:
    return ReportRequest(title="字体失败回归", sections=[{"heading": "概览"}])


def test_font_startup_failure_makes_health_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    restore_font_setup_state,
    tmp_path: Path,
):
    """A missing packaged font must be surfaced through health as a 503."""
    monkeypatch.setenv("CJK_FONT_DIR", str(tmp_path / "missing-fonts"))

    main._configure_fonts()

    response = main.health()
    assert response.status_code == 503
    assert "中文字体" in json.loads(response.body)["detail"]


def test_font_startup_failure_rejects_report_before_work_begins(
    monkeypatch: pytest.MonkeyPatch,
    restore_font_setup_state,
    tmp_path: Path,
):
    """An unready font setup must stop report creation before networking or rendering."""
    monkeypatch.setenv("CJK_FONT_DIR", str(tmp_path / "missing-fonts"))
    main._configure_fonts()
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda *args, **kwargs: pytest.fail("font failure must prevent HTTP client creation"),
    )
    monkeypatch.setattr(
        main,
        "_build_pdf",
        lambda *args, **kwargs: pytest.fail("font failure must prevent PDF rendering"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.create_report(_minimal_payload(), request=object()))

    assert exc_info.value.status_code == 503
    assert "中文字体" in exc_info.value.detail
