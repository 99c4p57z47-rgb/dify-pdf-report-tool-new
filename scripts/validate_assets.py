#!/usr/bin/env python3
"""Validate an internal image asset manifest before it is committed."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = (
    "asset_id",
    "path",
    "report_title",
    "publisher",
    "year",
    "source_page",
    "caption",
    "usage_scope",
)
DEFAULT_DATA_ASSET_TYPES = {"data_chart", "data_table"}
PACK_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate image assets and assets/manifest.json."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PACK_ROOT / "assets" / "manifest.json",
        help="JSON manifest path (default: assets/manifest.json)",
    )
    parser.add_argument(
        "--min-assets",
        type=non_negative_int,
        default=100,
        help="Minimum number of manifest records (default: 100)",
    )
    parser.add_argument(
        "--min-data-assets",
        type=non_negative_int,
        default=45,
        help="Minimum data_chart/data_table records (default: 45)",
    )
    parser.add_argument(
        "--data-asset-types",
        default=",".join(sorted(DEFAULT_DATA_ASSET_TYPES)),
        help="Comma-separated asset_type values counted as data assets",
    )
    return parser.parse_args()


def non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def load_records(manifest_path: Path) -> tuple[list[Any] | None, str | None]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"manifest not found: {manifest_path}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read manifest {manifest_path}: {exc}"

    records = payload.get("assets") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return None, "manifest must be a JSON array or an object with an 'assets' array"
    return records, None


def validate_records(
    records: list[Any],
    *,
    asset_root: Path,
    min_assets: int,
    min_data_assets: int,
    data_asset_types: set[str],
) -> list[str]:
    errors: list[str] = []
    if len(records) < min_assets:
        errors.append(f"asset count is {len(records)}; need at least {min_assets}")

    asset_ids: dict[str, int] = {}
    hashes: dict[str, tuple[int, str]] = {}
    data_asset_count = 0
    image_candidates: list[tuple[int, str, Path]] = []

    for index, record in enumerate(records):
        label = f"assets[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{label} must be an object")
            continue

        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            errors.append(f"{label} missing required fields: {', '.join(missing)}")

        asset_id = record.get("asset_id")
        if isinstance(asset_id, str) and asset_id.strip():
            asset_id = asset_id.strip()
            if asset_id in asset_ids:
                errors.append(f"duplicate asset_id: {asset_id} ({label} and assets[{asset_ids[asset_id]}])")
            else:
                asset_ids[asset_id] = index
        else:
            errors.append(f"{label}.asset_id must be a non-empty string")
            asset_id = f"record-{index}"

        validate_metadata(record, label, errors)
        if record.get("asset_type") in data_asset_types:
            data_asset_count += 1

        resolved_path = resolve_asset_path(record.get("path"), asset_root, label, errors)
        if resolved_path is not None:
            image_candidates.append((index, asset_id, resolved_path))

    if data_asset_count < min_data_assets:
        types = ", ".join(sorted(data_asset_types))
        errors.append(
            f"data asset count is {data_asset_count}; need at least {min_data_assets} "
            f"with asset_type in: {types}"
        )

    validate_images_and_hashes(image_candidates, hashes, errors)
    return errors


def validate_metadata(record: dict[str, Any], label: str, errors: list[str]) -> None:
    for field in ("report_title", "publisher", "caption"):
        if field in record and (not isinstance(record[field], str) or not record[field].strip()):
            errors.append(f"{label}.{field} must be a non-empty string")
    for field in ("year", "source_page"):
        value = record.get(field)
        if field in record and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            errors.append(f"{label}.{field} must be a positive integer")
    if "usage_scope" in record and record["usage_scope"] != "internal-analysis":
        errors.append(f"{label}.usage_scope must be 'internal-analysis'")


def resolve_asset_path(
    path_value: Any, asset_root: Path, label: str, errors: list[str]
) -> Path | None:
    if not isinstance(path_value, str) or not path_value.strip():
        errors.append(f"{label}.path must be a non-empty relative path")
        return None
    relative_path = Path(path_value)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        errors.append(f"{label}.path must stay inside the assets directory: {path_value}")
        return None
    candidate = asset_root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        errors.append(f"{label}.path does not exist: {path_value}")
        return None
    if not resolved.is_relative_to(asset_root.resolve()) or not resolved.is_file():
        errors.append(f"{label}.path must be a regular file inside assets/: {path_value}")
        return None
    return resolved


def validate_images_and_hashes(
    image_candidates: list[tuple[int, str, Path]],
    hashes: dict[str, tuple[int, str]],
    errors: list[str],
) -> None:
    if not image_candidates:
        return
    try:
        from PIL import Image
    except ImportError:
        errors.append("Pillow is required to verify image readability; install it with: python -m pip install Pillow")
        return

    for index, asset_id, image_path in image_candidates:
        try:
            with Image.open(image_path) as image:
                image.verify()
        except Exception as exc:
            errors.append(f"assets[{index}] image is not readable: {image_path.name} ({exc})")
            continue

        sha256 = file_sha256(image_path)
        if sha256 in hashes:
            first_index, first_id = hashes[sha256]
            errors.append(
                f"duplicate sha256: {asset_id} (assets[{index}]) matches "
                f"{first_id} (assets[{first_index}])"
            )
        else:
            hashes[sha256] = (index, asset_id)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    records, load_error = load_records(args.manifest)
    if load_error:
        print(f"ERROR: {load_error}", file=sys.stderr)
        return 2

    data_asset_types = {item.strip() for item in args.data_asset_types.split(",") if item.strip()}
    if not data_asset_types:
        print("ERROR: --data-asset-types must include at least one value", file=sys.stderr)
        return 2

    errors = validate_records(
        records,
        asset_root=args.manifest.parent,
        min_assets=args.min_assets,
        min_data_assets=args.min_data_assets,
        data_asset_types=data_asset_types,
    )
    if errors:
        print(f"Validation failed with {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        f"Validation passed: {len(records)} assets, at least {args.min_data_assets} "
        "data_chart/data_table assets required."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
