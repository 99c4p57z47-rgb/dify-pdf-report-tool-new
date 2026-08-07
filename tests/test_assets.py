from __future__ import annotations

import asyncio
import io
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.assets import (
    AssetManifestError,
    AssetRegistry,
    _download_image,
    resolve_asset_or_warning,
    resolve_image,
)
from app.models import ImageSpec
from scripts.build_asset_manifest import build_manifest


def _png_bytes(size: tuple[int, int] = (20, 20)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGBA", size, (20, 40, 60, 128)).save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def asset_fixture(tmp_path: Path):
    root = tmp_path / "assets"
    root.mkdir()
    (root / "figure.png").write_bytes(_png_bytes())
    manifest = root / "manifest.json"

    def write_manifest(**overrides):
        record = {
            "asset_id": "report_012_p018_fig01",
            "path": "figure.png",
            "report_title": "报告名称",
            "publisher": "发布机构",
            "year": 2026,
            "source_page": 18,
            "caption": "可信图注",
            "usage_scope": "internal-analysis",
        }
        record.update(overrides)
        manifest.write_text(json.dumps({"assets": [record]}, ensure_ascii=False), encoding="utf-8")
        return SimpleNamespace(root=root, manifest=manifest, write_manifest=write_manifest)

    return write_manifest()


def test_asset_registry_returns_trusted_metadata(asset_fixture):
    registry = AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)
    item = registry.resolve("report_012_p018_fig01")
    assert item.report_title == "报告名称"
    assert item.source_page == 18
    assert item.caption == "可信图注"


def test_manifest_rejects_path_escape(asset_fixture):
    asset_fixture.write_manifest(path="../secret.png")
    with pytest.raises(AssetManifestError, match="资产目录"):
        AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)


@pytest.mark.parametrize("missing", ["report_title", "publisher", "year", "source_page", "caption"])
def test_manifest_requires_complete_commercial_metadata(asset_fixture, missing):
    item = json.loads(asset_fixture.manifest.read_text(encoding="utf-8"))["assets"][0]
    item.pop(missing)
    asset_fixture.manifest.write_text(json.dumps({"assets": [item]}), encoding="utf-8")
    with pytest.raises(AssetManifestError, match=missing):
        AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)


def test_unknown_asset_is_a_warning_not_a_crash(asset_fixture, tmp_path):
    registry = AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)
    result = resolve_asset_or_warning("missing", registry, tmp_path)
    assert result.path is None
    assert "missing" in result.warning


def test_damaged_asset_is_a_warning_not_a_crash(asset_fixture, tmp_path):
    registry = AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)
    (asset_fixture.root / "figure.png").write_bytes(b"not an image")
    result = resolve_asset_or_warning("report_012_p018_fig01", registry, tmp_path)
    assert result.path is None
    assert "损坏" in result.warning


class _Response:
    def __init__(self, status_code=200, headers=None, content=b"", chunks=None, extensions=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.chunks = chunks if chunks is not None else [content]
        self.extensions = extensions or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk


class _StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class _NetworkStream:
    def __init__(self, peer):
        self.peer = peer

    def get_extra_info(self, key):
        return self.peer if key == "server_addr" else None


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)

    def stream(self, method, url, follow_redirects=False):
        return _StreamContext(self.responses.pop(0))


def _url_spec(url="https://images.example.com/a.png"):
    return SimpleNamespace(
        asset_id=None,
        url=url,
        caption="请求图注",
        report_title="网络报告",
        publisher="网络发布者",
        year=2025,
        source_page=9,
    )


def test_download_blocks_private_dns(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="restricted"):
        asyncio.run(_download_image(_Client([]), _url_spec(), tmp_path, 0))


def test_download_rejects_fifth_redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])
    redirects = [_Response(302, {"location": "/next"}) for _ in range(4)]
    with pytest.raises(ValueError, match="Too many"):
        asyncio.run(_download_image(_Client(redirects), _url_spec(), tmp_path, 0))


def test_download_rejects_non_image_content_type(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])
    response = _Response(headers={"content-type": "text/html"}, content=b"hello")
    with pytest.raises(ValueError, match="did not return an image"):
        asyncio.run(_download_image(_Client([response]), _url_spec(), tmp_path, 0))


def test_download_rejects_excess_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])
    response = _Response(headers={"content-type": "image/png"}, chunks=[b"x" * 6, b"x" * 6])
    with pytest.raises(ValueError, match="size limit"):
        asyncio.run(_download_image(_Client([response]), _url_spec(), tmp_path, 0, max_image_bytes=10))


def test_download_rejects_private_connected_peer(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])
    response = _Response(
        headers={"content-type": "image/png"},
        content=_png_bytes(),
        extensions={"network_stream": _NetworkStream(("127.0.0.1", 443))},
    )
    with pytest.raises(ValueError, match="restricted"):
        asyncio.run(_download_image(_Client([response]), _url_spec(), tmp_path, 0))


def test_asset_metadata_overrides_request_metadata(asset_fixture, tmp_path):
    registry = AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)
    spec = SimpleNamespace(
        asset_id="report_012_p018_fig01",
        url=None,
        caption="不可信请求图注",
        report_title="不可信报告名",
        publisher="不可信发布者",
        year=1999,
        source_page=1,
    )
    result = asyncio.run(resolve_image(spec, registry, _Client([]), tmp_path))
    assert (result.caption, result.report_title, result.publisher, result.year, result.source_page) == (
        "可信图注", "报告名称", "发布机构", 2026, 18
    )


def test_manifest_rejects_parent_token_even_when_it_resolves_inside_root(asset_fixture):
    nested = asset_fixture.root / "nested"
    nested.mkdir()
    (nested / "figure.png").write_bytes(_png_bytes())
    asset_fixture.write_manifest(path="nested/../figure.png")
    with pytest.raises(AssetManifestError, match=r"\.\."):
        AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)


def test_manifest_rejects_symlink_in_asset_path(asset_fixture):
    actual = asset_fixture.root / "actual"
    actual.mkdir()
    (actual / "figure.png").write_bytes(_png_bytes())
    (asset_fixture.root / "linked").symlink_to(actual, target_is_directory=True)
    asset_fixture.write_manifest(path="linked/figure.png")
    with pytest.raises(AssetManifestError, match="symlink"):
        AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)


def test_lookup_rechecks_for_symlink_swap(asset_fixture):
    registry = AssetRegistry.from_manifest(asset_fixture.root, asset_fixture.manifest)
    original = asset_fixture.root / "figure.png"
    moved = asset_fixture.root / "moved.png"
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(AssetManifestError, match="symlink"):
        registry.resolve("report_012_p018_fig01")


def test_builder_copies_supported_images_with_deterministic_sorted_ids(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "p002_second.jpg").write_bytes(_png_bytes())
    (source / "p001_first.png").write_bytes(_png_bytes())
    (source / "p003_notes.txt").write_text("not an image", encoding="utf-8")
    output = tmp_path / "output"
    records = build_manifest(
        source=source,
        output_root=output,
        report_id="report_012",
        report_title="报告名称",
        publisher="发布机构",
        year=2026,
    )
    assert [item["asset_id"] for item in records] == [
        "report_012_p001_first",
        "report_012_p002_second",
    ]
    assert [item["source_page"] for item in records] == [1, 2]
    assert records[0]["caption"] == "报告名称｜原报告第 1 页图像"
    assert (output / "report_012" / "p001_first.png").is_file()


def test_builder_refuses_image_missing_from_explicit_source_page_map(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "p001_first.png").write_bytes(_png_bytes())
    page_map = tmp_path / "pages.json"
    page_map.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="无法确定原始页码"):
        build_manifest(
            source=source,
            output_root=tmp_path / "output",
            report_id="report_012",
            report_title="报告名称",
            publisher="发布机构",
            year=2026,
            source_page_map=page_map,
        )


@pytest.mark.parametrize("report_id", ["../report", "report/path", "report id", "-report", "report_"])
def test_builder_rejects_unstable_report_id(tmp_path, report_id):
    source = tmp_path / "source"
    source.mkdir()
    (source / "p001_first.png").write_bytes(_png_bytes())
    with pytest.raises(ValueError, match="report_id"):
        build_manifest(source=source, output_root=tmp_path / "output", report_id=report_id,
                       report_title="报告", publisher="机构", year=2026)


def test_builder_rejects_source_symlink(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "p001_first.png").write_bytes(_png_bytes())
    source = tmp_path / "source"
    source.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build_manifest(source=source, output_root=tmp_path / "output", report_id="report_012",
                       report_title="报告", publisher="机构", year=2026)


def test_builder_rejects_symlink_in_source_root_path_chain(tmp_path):
    actual_parent = tmp_path / "actual_parent"
    source = actual_parent / "source"
    source.mkdir(parents=True)
    (source / "p001_first.png").write_bytes(_png_bytes())
    linked_parent = tmp_path / "linked_parent"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build_manifest(source=linked_parent / "source", output_root=tmp_path / "output",
                       report_id="report_012", report_title="报告", publisher="机构", year=2026)


def test_builder_rejects_symlinked_source_file(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    actual = tmp_path / "p001_actual.png"
    actual.write_bytes(_png_bytes())
    (source / "p001_first.png").symlink_to(actual)
    with pytest.raises(ValueError, match="symlink"):
        build_manifest(source=source, output_root=tmp_path / "output", report_id="report_012",
                       report_title="报告", publisher="机构", year=2026)


def test_builder_rejects_deterministic_asset_id_collision(tmp_path):
    source = tmp_path / "source"
    (source / "a").mkdir(parents=True)
    (source / "b").mkdir()
    (source / "a" / "p001_same.png").write_bytes(_png_bytes())
    (source / "b" / "p001_same.jpg").write_bytes(_png_bytes())
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="asset_id 冲突"):
        build_manifest(source=source, output_root=output, report_id="report_012",
                       report_title="报告", publisher="机构", year=2026)
    assert not (output / "report_012").exists()


@pytest.mark.parametrize("filename", ["crop2024.png", "p000_cover.png"])
def test_builder_rejects_invalid_page_token(tmp_path, filename):
    source = tmp_path / "source"
    source.mkdir()
    (source / filename).write_bytes(_png_bytes())
    with pytest.raises(ValueError, match="原始页码"):
        build_manifest(source=source, output_root=tmp_path / "output", report_id="report_012",
                       report_title="报告", publisher="机构", year=2026)


@pytest.mark.parametrize(
    ("filename", "expected_id"),
    [
        ("p001--figure.png", "report_012_p001_figure"),
        ("p001__figure.png", "report_012_p001_figure"),
        ("p001-.png", "report_012_p001"),
    ],
)
def test_builder_normalizes_asset_id_for_image_spec(tmp_path, filename, expected_id):
    source = tmp_path / "source"
    source.mkdir()
    (source / filename).write_bytes(_png_bytes())
    records = build_manifest(source=source, output_root=tmp_path / "output", report_id="report_012",
                             report_title="报告", publisher="机构", year=2026)
    assert records[0]["asset_id"] == expected_id
    assert ImageSpec(asset_id=records[0]["asset_id"], caption="图").asset_id == expected_id


def test_builder_rejects_collision_after_separator_normalization_before_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "p001--figure.png").write_bytes(_png_bytes())
    (source / "p001__figure.png").write_bytes(_png_bytes())
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="asset_id 冲突"):
        build_manifest(source=source, output_root=output, report_id="report_012",
                       report_title="报告", publisher="机构", year=2026)
    assert not (output / "report_012").exists()


def test_download_rejects_http_initial_url(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(_download_image(_Client([]), _url_spec("http://images.example.com/a.png"), tmp_path, 0))


def test_download_rejects_https_to_http_redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))])
    response = _Response(302, {"location": "http://images.example.com/next.png"})
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(_download_image(_Client([response]), _url_spec(), tmp_path, 0))
