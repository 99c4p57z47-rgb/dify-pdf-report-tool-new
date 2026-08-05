from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PAGE_PATTERN = re.compile(r"(?:^|[_-])p(\d{3,6})(?=$|[_-])", re.IGNORECASE)
STABLE_ID_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*")


def _load_page_map(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--source-page-map 必须是 JSON 对象")
    return {str(key): int(value) for key, value in payload.items()}


def build_manifest(
    *, source: Path, output_root: Path, report_id: str, report_title: str,
    publisher: str, year: int, source_page_map: Path | None = None,
) -> list[dict]:
    if not STABLE_ID_PATTERN.fullmatch(report_id):
        raise ValueError("report_id 必须是稳定 ID")
    _reject_existing_path_symlinks(source, "source")
    source = source.resolve(strict=True)
    if output_root.is_symlink():
        raise ValueError("output_root 不能是 symlink")
    output_root.mkdir(parents=True, exist_ok=True)
    output_root = output_root.resolve(strict=True)
    page_map = _load_page_map(source_page_map)
    candidates = sorted(path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES)
    records: list[dict] = []
    planned: list[tuple[Path, Path]] = []
    seen_ids: set[str] = set()
    for path in candidates:
        relative = path.relative_to(source)
        _reject_symlink_chain(source, relative, "source")
        mapped = page_map.get(relative.as_posix())
        match = PAGE_PATTERN.search(path.stem)
        if source_page_map is not None and mapped is None:
            raise ValueError(f"无法确定原始页码: {relative}")
        if mapped is None and match is None:
            raise ValueError(f"无法确定原始页码: {relative}")
        source_page = mapped if mapped is not None else int(match.group(1))
        if source_page < 1:
            raise ValueError(f"无法确定原始页码: {relative}")
        safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem)
        safe_stem = re.sub(r"[_-]+", "_", safe_stem).strip("_-").lower()
        asset_id = f"{report_id}_{safe_stem}"
        if not STABLE_ID_PATTERN.fullmatch(asset_id):
            raise ValueError(f"无法为文件生成稳定 asset_id: {relative}")
        if asset_id in seen_ids:
            raise ValueError(f"asset_id 冲突: {asset_id}")
        seen_ids.add(asset_id)
        destination = output_root / report_id / relative
        _ensure_output_path(output_root, destination)
        planned.append((path, destination))
        records.append({
            "asset_id": asset_id,
            "path": destination.relative_to(output_root).as_posix(),
            "report_title": report_title,
            "publisher": publisher,
            "year": year,
            "source_page": source_page,
            "caption": f"{report_title}｜原报告第 {source_page} 页图像",
            "usage_scope": "internal-analysis",
        })
    for path, destination in planned:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ensure_output_path(output_root, destination)
        shutil.copy2(path, destination)
    records.sort(key=lambda item: item["asset_id"])
    manifest_path = output_root / "manifest.json"
    _ensure_output_path(output_root, manifest_path)
    existing = []
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = payload.get("assets", []) if isinstance(payload, dict) else payload
    ids = {item["asset_id"] for item in records}
    merged = sorted([item for item in existing if item.get("asset_id") not in ids] + records, key=lambda item: item["asset_id"])
    manifest_path.write_text(json.dumps({"assets": merged}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records


def _reject_symlink_chain(root: Path, relative: Path, label: str) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} 路径链不能包含 symlink")


def _reject_existing_path_symlinks(path: Path, label: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} 路径链不能包含 symlink")


def _ensure_output_path(root: Path, target: Path) -> None:
    relative = target.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("输出路径链不能包含 symlink")
    if not target.resolve(strict=False).is_relative_to(root):
        raise ValueError("输出路径不能离开 output_root")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the trusted PDF image asset manifest offline.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--report-title", required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--source-page-map", type=Path)
    args = parser.parse_args()
    build_manifest(**vars(args))


if __name__ == "__main__":
    main()
