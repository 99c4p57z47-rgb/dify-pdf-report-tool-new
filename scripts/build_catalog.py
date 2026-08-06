#!/usr/bin/env python3
"""Build Markdown and JSONL knowledge cards from an image asset manifest."""

from __future__ import annotations

import argparse
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
PACK_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create knowledge_cards/asset_catalog.md and .jsonl from a manifest."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PACK_ROOT / "assets" / "manifest.json",
        help="JSON manifest path (default: assets/manifest.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PACK_ROOT / "knowledge_cards",
        help="Directory for catalog output (default: knowledge_cards/)",
    )
    return parser.parse_args()


def load_records(manifest_path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"manifest not found: {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read manifest {manifest_path}: {exc}") from exc
    records = payload.get("assets") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("manifest must be a JSON array or an object with an 'assets' array")

    validated: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"assets[{index}] must be an object")
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise ValueError(f"assets[{index}] missing required fields: {', '.join(missing)}")
        if record["usage_scope"] != "internal-analysis":
            raise ValueError(f"assets[{index}].usage_scope must be 'internal-analysis'")
        validated.append(record)
    return validated


def markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def build_markdown(records: list[dict[str, Any]], manifest_path: Path) -> str:
    lines = [
        "# Internal Image Asset Catalog",
        "",
        "Generated from `assets/manifest.json`. This catalog is for internal analysis only.",
        "",
        "| Asset ID | Type | Category | Report | Publisher | Year | Page | Caption | Image path | Data path |",
        "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for record in records:
        values = (
            record["asset_id"],
            record.get("asset_type", ""),
            record.get("category", ""),
            record["report_title"],
            record["publisher"],
            record["year"],
            record["source_page"],
            record["caption"],
            record["path"],
            record.get("data_path", ""),
        )
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    return "\n".join(lines) + "\n"


def write_catalog(records: list[dict[str, Any]], manifest_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "asset_catalog.md").write_text(
        build_markdown(records, manifest_path), encoding="utf-8"
    )
    with (output_dir / "asset_catalog.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    try:
        records = load_records(args.manifest)
        write_catalog(records, args.manifest, args.output_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(records)} catalog record(s) to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
