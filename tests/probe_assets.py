"""Focused no-pytest security probes for the bundled Python runtime."""
from __future__ import annotations

import asyncio
import io
import json
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.assets as assets
from app.assets import AssetManifestError, AssetRegistry, _download_image, _validate_remote_host
from scripts.build_asset_manifest import STABLE_ID_PATTERN, build_manifest


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGBA", (20, 20), (1, 2, 3, 128)).save(stream, "PNG")
    return stream.getvalue()


def raises(call, expected: str) -> None:
    try:
        call()
    except Exception as exc:
        assert expected in str(exc), (expected, str(exc))
        return
    raise AssertionError(f"expected exception containing {expected!r}")


class Response:
    def __init__(self, status=200, headers=None, chunks=None, extensions=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks or []
        self.extensions = extensions or {}

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk


class StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class Client:
    def __init__(self, responses):
        self.responses = list(responses)

    def stream(self, *args, **kwargs):
        return StreamContext(self.responses.pop(0))


class PrivatePeer:
    def get_extra_info(self, key):
        return ("127.0.0.1", 443) if key == "server_addr" else None


def main() -> None:
    passed: list[str] = []
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name).resolve()
        root = temp / "assets"
        root.mkdir()
        (root / "figure.png").write_bytes(png())
        (root / "nested").mkdir()
        manifest = root / "manifest.json"
        record = {"asset_id": "asset_1", "path": "figure.png", "report_title": "t",
                  "publisher": "p", "year": 2026, "source_page": 1, "caption": "c",
                  "usage_scope": "internal-analysis"}

        def load(path: str):
            manifest.write_text(json.dumps({"assets": [{**record, "path": path}]}), encoding="utf-8")
            return AssetRegistry.from_manifest(root, manifest)

        raises(lambda: load("nested/../figure.png"), "..")
        passed.append("manifest parent token")
        actual = root / "actual"
        actual.mkdir()
        (actual / "f.png").write_bytes(png())
        (root / "linked").symlink_to(actual, target_is_directory=True)
        raises(lambda: load("linked/f.png"), "symlink")
        passed.append("manifest symlink")
        registry = load("figure.png")
        (root / "figure.png").rename(root / "moved.png")
        (root / "figure.png").symlink_to(root / "moved.png")
        raises(lambda: registry.resolve("asset_1"), "symlink")
        passed.append("lookup symlink swap")

        source = temp / "source"
        source.mkdir()
        (source / "p001_first.png").write_bytes(png())
        rows = build_manifest(source=source, output_root=temp / "out", report_id="report_012",
                              report_title="t", publisher="p", year=2026)
        assert rows[0]["source_page"] == 1
        passed.append("builder valid")
        raises(lambda: build_manifest(source=source, output_root=temp / "bad", report_id="../escape",
                                      report_title="t", publisher="p", year=2026), "report_id")
        passed.append("stable report id")
        invalid = temp / "invalid"
        invalid.mkdir()
        (invalid / "crop2024.png").write_bytes(png())
        raises(lambda: build_manifest(source=invalid, output_root=temp / "bad2", report_id="report_1",
                                      report_title="t", publisher="p", year=2026), "原始页码")
        passed.append("page token boundary")
        (invalid / "crop2024.png").unlink()
        (invalid / "p000_bad.png").write_bytes(png())
        raises(lambda: build_manifest(source=invalid, output_root=temp / "bad3", report_id="report_1",
                                      report_title="t", publisher="p", year=2026), "原始页码")
        passed.append("positive page")

        normalized_expected = {
            "p001--figure.png": "report_012_p001_figure",
            "p001__figure.png": "report_012_p001_figure",
            "p001-.png": "report_012_p001",
        }
        for filename, expected_id in normalized_expected.items():
            normalized_source = temp / f"normalized-{filename.replace('.', '_')}"
            normalized_source.mkdir()
            (normalized_source / filename).write_bytes(png())
            normalized_rows = build_manifest(
                source=normalized_source,
                output_root=temp / f"normalized-out-{filename.replace('.', '_')}",
                report_id="report_012",
                report_title="t",
                publisher="p",
                year=2026,
            )
            assert normalized_rows[0]["asset_id"] == expected_id
            assert STABLE_ID_PATTERN.fullmatch(normalized_rows[0]["asset_id"])
        passed.append("stable separator normalization")

        normalized_collision = temp / "normalized-collision"
        normalized_collision.mkdir()
        (normalized_collision / "p001--figure.png").write_bytes(png())
        (normalized_collision / "p001__figure.png").write_bytes(png())
        normalized_collision_out = temp / "normalized-collision-out"
        raises(lambda: build_manifest(
            source=normalized_collision,
            output_root=normalized_collision_out,
            report_id="report_012",
            report_title="t",
            publisher="p",
            year=2026,
        ), "asset_id 冲突")
        assert not (normalized_collision_out / "report_012").exists()
        passed.append("normalized collision atomic refusal")

        collision = temp / "collision"
        (collision / "a").mkdir(parents=True)
        (collision / "b").mkdir()
        (collision / "a" / "p001_same.png").write_bytes(png())
        (collision / "b" / "p001_same.jpg").write_bytes(png())
        collision_out = temp / "collision-out"
        raises(lambda: build_manifest(source=collision, output_root=collision_out, report_id="report_1",
                                      report_title="t", publisher="p", year=2026), "asset_id 冲突")
        assert not (collision_out / "report_1").exists()
        passed.append("collision atomic refusal")

        original_dns = assets.socket.getaddrinfo
        assets.socket.getaddrinfo = lambda *args: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 443))]
        try:
            raises(lambda: asyncio.run(_validate_remote_host("http://example.com/a.png")), "HTTPS")
            passed.append("HTTP rejected")
            spec = SimpleNamespace(url="https://example.com/a.png")
            raises(lambda: asyncio.run(_download_image(
                Client([Response(302, {"location": "http://example.com/b.png"})]), spec, temp, 0)), "HTTPS")
            passed.append("redirect downgrade")
            raises(lambda: asyncio.run(_download_image(
                Client([Response(headers={"content-type": "image/png"}, chunks=[b"123456", b"789012"])]),
                spec, temp, 0, max_image_bytes=10)), "size limit")
            passed.append("stream byte cap")
            raises(lambda: asyncio.run(_download_image(
                Client([Response(headers={"content-type": "image/png"}, chunks=[png()],
                                 extensions={"network_stream": PrivatePeer()})]), spec, temp, 0)), "restricted")
            passed.append("connected peer IP")
        finally:
            assets.socket.getaddrinfo = original_dns

        deployed = Path(__file__).resolve().parents[1] / "assets"
        resolved = AssetRegistry.from_manifest(deployed, deployed / "manifest.json").resolve(
            "ht_generated_chart_001"
        )
        assert resolved.path and resolved.source_page == 1
        passed.append("deployable manifest")

    print(f"FOCUSED ASSET PROBES: {len(passed)}/{len(passed)} PASS")
    for name in passed:
        print(f"PASS {name}")


if __name__ == "__main__":
    main()
