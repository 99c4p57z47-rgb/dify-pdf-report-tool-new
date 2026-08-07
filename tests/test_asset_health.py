from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from fastapi import HTTPException


def _load_main(monkeypatch, tmp_path, *, preview: bool = False):
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    (asset_root / "manifest.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("PDF_ASSET_DIR", str(asset_root))
    if preview:
        monkeypatch.setenv("ENABLE_ASSET_PREVIEW", "true")
    else:
        monkeypatch.delenv("ENABLE_ASSET_PREVIEW", raising=False)
    import app.main as main
    return importlib.reload(main)


def test_bad_manifest_makes_health_unready(monkeypatch, tmp_path):
    main = _load_main(monkeypatch, tmp_path)
    response = main.health()
    assert response.status_code == 503
    assert "asset" in json.loads(response.body)["detail"].lower()


def test_bad_manifest_rejects_report_before_rendering(monkeypatch, tmp_path):
    main = _load_main(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.create_report(None, None))
    assert error.value.status_code == 503
    assert "asset" in error.value.detail.lower()


def test_asset_preview_is_not_mounted_by_default(monkeypatch, tmp_path):
    main = _load_main(monkeypatch, tmp_path)
    assert "/assets" not in {getattr(route, "path", None) for route in main.app.routes}


def test_asset_preview_can_be_explicitly_enabled(monkeypatch, tmp_path):
    main = _load_main(monkeypatch, tmp_path, preview=True)
    assert "/assets" in {getattr(route, "path", None) for route in main.app.routes}
