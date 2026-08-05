from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:
    import httpx

    from app.models import ImageSpec


MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))
_REQUIRED_KEYS = {
    "asset_id", "path", "report_title", "publisher", "year",
    "source_page", "caption", "usage_scope",
}
_TRUSTED_TEXT_LIMITS = {
    "report_title": 300,
    "publisher": 200,
    "caption": 600,
}


class AssetManifestError(ValueError):
    """Raised when a trusted asset manifest violates its security contract."""


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    path: Path
    report_title: str
    publisher: str
    year: int
    source_page: int
    caption: str
    usage_scope: str
    relative_path: Path


@dataclass(frozen=True)
class ResolvedImage:
    path: Path | None
    caption: str
    report_title: str | None
    publisher: str | None
    year: int | None
    source_page: int | None
    warning: str = ""


class AssetRegistry:
    def __init__(self, asset_root: Path, records: dict[str, AssetRecord]):
        self.asset_root = asset_root.resolve()
        self._records = records

    @classmethod
    def empty(cls, asset_root: Path) -> "AssetRegistry":
        return cls(asset_root, {})

    @classmethod
    def from_manifest(cls, asset_root: Path, manifest_path: Path) -> "AssetRegistry":
        if asset_root.is_symlink():
            raise AssetManifestError("资产目录不能是 symlink")
        root = asset_root.resolve()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AssetManifestError(f"无法读取资产清单: {exc}") from exc
        items = payload.get("assets") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise AssetManifestError("资产清单必须包含 assets 数组")

        records: dict[str, AssetRecord] = {}
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise AssetManifestError(f"assets[{index}] 必须是对象")
            missing = sorted(_REQUIRED_KEYS - item.keys())
            if missing:
                raise AssetManifestError(f"assets[{index}] 缺少字段: {', '.join(missing)}")
            asset_id = _required_text(item, "asset_id", index)
            if asset_id in records:
                raise AssetManifestError(f"asset_id 重复: {asset_id}")
            if item["usage_scope"] != "internal-analysis":
                raise AssetManifestError(f"assets[{index}].usage_scope 必须为 internal-analysis")
            year = _positive_int(item, "year", index)
            source_page = _positive_int(item, "source_page", index)
            relative = Path(_required_text(item, "path", index))
            if relative.is_absolute():
                raise AssetManifestError(f"assets[{index}].path 必须位于资产目录内")
            if ".." in relative.parts:
                raise AssetManifestError(f"assets[{index}].path 不能包含 ..")
            resolved = _secure_regular_file(root, relative, f"assets[{index}].path")
            records[asset_id] = AssetRecord(
                asset_id=asset_id,
                path=resolved,
                report_title=_required_text(
                    item,
                    "report_title",
                    index,
                    max_length=_TRUSTED_TEXT_LIMITS["report_title"],
                ),
                publisher=_required_text(
                    item,
                    "publisher",
                    index,
                    max_length=_TRUSTED_TEXT_LIMITS["publisher"],
                ),
                year=year,
                source_page=source_page,
                caption=_required_text(
                    item,
                    "caption",
                    index,
                    max_length=_TRUSTED_TEXT_LIMITS["caption"],
                ),
                usage_scope="internal-analysis",
                relative_path=relative,
            )
        return cls(root, records)

    def resolve(self, asset_id: str) -> ResolvedImage:
        try:
            record = self._records[asset_id]
        except KeyError as exc:
            raise KeyError(f"未知资产 ID: {asset_id}") from exc
        current_path = _secure_regular_file(self.asset_root, record.relative_path, f"资产 {asset_id}")
        return ResolvedImage(
            path=current_path,
            caption=record.caption,
            report_title=record.report_title,
            publisher=record.publisher,
            year=record.year,
            source_page=record.source_page,
        )


def _required_text(
    item: dict[str, Any],
    key: str,
    index: int,
    *,
    max_length: int | None = None,
) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssetManifestError(f"assets[{index}].{key} 必须是非空字符串")
    value = value.strip()
    if max_length is not None and len(value) > max_length:
        raise AssetManifestError(
            f"assets[{index}].{key} 不能超过 {max_length} 个字符"
        )
    return value


def _positive_int(item: dict[str, Any], key: str, index: int) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AssetManifestError(f"assets[{index}].{key} 必须是正整数")
    return value


def _secure_regular_file(root: Path, relative: Path, label: str) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AssetManifestError(f"{label} 路径链不能包含 symlink")
    candidate = current.resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise AssetManifestError(f"{label} 不能离开资产目录")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise AssetManifestError(f"{label} 资产文件不存在: {relative}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise AssetManifestError(f"{label} 必须是资产目录内的普通文件")
    return resolved


def resolve_asset_or_warning(asset_id: str, registry: AssetRegistry, work_dir: Path) -> ResolvedImage:
    del work_dir
    try:
        resolved = registry.resolve(asset_id)
        if resolved.path is None:
            return resolved
        from PIL import Image

        with Image.open(resolved.path) as image:
            image.verify()
        return resolved
    except Exception as exc:
        return ResolvedImage(None, "", None, None, None, None, f"资产 {asset_id} 缺失或损坏：{exc}")


async def _validate_remote_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only HTTPS image URLs are supported")
    infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    for info in infos:
        _reject_restricted_ip(info[4][0], parsed.hostname)


def _reject_restricted_ip(value: str, hostname: str) -> None:
    address = ipaddress.ip_address(value)
    if (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
    ):
        raise ValueError(f"Image host resolves to a restricted address: {hostname}")


def _validate_peer_ip(response: Any, hostname: str) -> None:
    stream = response.extensions.get("network_stream") if hasattr(response, "extensions") else None
    if stream is None or not hasattr(stream, "get_extra_info"):
        return
    for key in ("server_addr", "peername"):
        peer = stream.get_extra_info(key)
        if peer:
            value = peer[0] if isinstance(peer, tuple) else peer
            _reject_restricted_ip(str(value), hostname)
            return


async def _download_image(
    client: "httpx.AsyncClient",
    spec: "ImageSpec",
    directory: Path,
    index: int,
    *,
    max_image_bytes: int = MAX_IMAGE_BYTES,
) -> Path:
    if spec.url is None:
        raise ValueError("asset_id image cannot be downloaded")
    url = str(spec.url)
    for _ in range(4):
        await _validate_remote_host(url)
        parsed = urlparse(url)
        async with client.stream("GET", url, follow_redirects=False) as response:
            _validate_peer_ip(response, parsed.hostname or "")
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Image redirect has no location")
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("image/"):
                raise ValueError(f"URL did not return an image: {content_type or 'unknown content type'}")
            declared = response.headers.get("content-length")
            if declared and int(declared) > max_image_bytes:
                raise ValueError("Image exceeds size limit")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > max_image_bytes:
                    raise ValueError("Image exceeds size limit")
        from PIL import Image, ImageOps

        raw = Image.open(io.BytesIO(bytes(content)))
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((2400, 1800), Image.Resampling.LANCZOS)
        path = directory / f"image_{index:03d}.jpg"
        image.save(path, "JPEG", quality=90, optimize=True)
        return path
    raise ValueError("Too many image redirects")


async def resolve_image(
    spec: "ImageSpec",
    registry: AssetRegistry,
    client: "httpx.AsyncClient",
    work_dir: Path,
) -> ResolvedImage:
    if spec.asset_id is not None:
        return resolve_asset_or_warning(spec.asset_id, registry, work_dir)
    try:
        index = sum(1 for _ in work_dir.glob("image_*.jpg"))
        path = await _download_image(client, spec, work_dir, index)
        return ResolvedImage(
            path=path,
            caption=spec.caption,
            report_title=spec.report_title,
            publisher=spec.publisher,
            year=spec.year,
            source_page=spec.source_page,
        )
    except Exception as exc:
        return ResolvedImage(None, spec.caption, spec.report_title, spec.publisher, spec.year, spec.source_page, str(exc))
