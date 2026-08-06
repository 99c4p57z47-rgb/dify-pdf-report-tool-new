#!/usr/bin/env python3
"""Assemble the selected original and derived visuals into the GitHub asset pack."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[3]
PACK_ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SELECTION = WORKSPACE / "work" / "image_pack_selection.jsonl"
DERIVED_MANIFEST = WORKSPACE / "work" / "derived_visuals" / "manifest.jsonl"

CATEGORY_MAP = {
    "原始数据图表": ("data_charts", "data_chart"),
    "色彩材质": ("color_material", "reference_image"),
    "产品空间": ("product_space", "reference_image"),
    "消费_品类_渠道": ("consumer_market", "reference_image"),
    "洗护_材料_技术": ("laundry_technology", "reference_image"),
}


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return records


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clipped(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip(" ,，。；;")


def publication_year(value: object, title: object) -> tuple[int, str]:
    if str(value or "").strip():
        return int(value), "manifest"
    title_text = str(title or "")
    four_digit = re.findall(r"20\d{2}", title_text)
    if four_digit:
        return int(four_digit[0]), "report_title"
    if re.search(r"(?:^|\D)26\s*[&和至—-]\s*27(?:\D|$)", title_text):
        return 2026, "report_title_season_2026_2027"
    raise ValueError(f"无法确定报告年份: {title_text}")


def copy_originals() -> list[dict]:
    counters: Counter[str] = Counter()
    output: list[dict] = []
    for item in load_jsonl(ORIGINAL_SELECTION):
        source = Path(item["source_path"]).resolve(strict=True)
        category, asset_type = CATEGORY_MAP[item["category"]]
        counters[category] += 1
        asset_id = f"ht_{category}_{counters[category]:03d}"
        destination = PACK_ROOT / "assets" / category / f"{asset_id}{source.suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        year, year_basis = publication_year(item.get("year"), item.get("report_title"))
        record = {
            "asset_id": asset_id,
            "path": destination.relative_to(PACK_ROOT / "assets").as_posix(),
            "report_title": clipped(item["report_title"], 300),
            "publisher": clipped(item["publisher"], 200),
            "year": year,
            "year_basis": year_basis,
            "source_page": int(item["source_page"]),
            "caption": clipped(item["caption"], 560),
            "usage_scope": "internal-analysis",
            "category": category,
            "asset_type": asset_type,
            "provenance": "original_report_image",
            "sha256": sha256(destination),
            "selection_reason": clipped(item.get("rationale", ""), 300),
        }
        output.append(record)
    return output


def copy_derived(start_index: int = 1) -> list[dict]:
    output: list[dict] = []
    for index, item in enumerate(load_jsonl(DERIVED_MANIFEST), start_index):
        source = Path(item["asset_path"]).resolve(strict=True)
        data_source = Path(item["data_path"]).resolve(strict=True)
        asset_id = f"ht_generated_chart_{index:03d}"
        destination = PACK_ROOT / "assets" / "data_charts" / f"{asset_id}{source.suffix.lower()}"
        data_destination = PACK_ROOT / "assets" / "data" / "generated_chart_data" / f"{asset_id}.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        data_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        shutil.copy2(data_source, data_destination)
        record = {
            "asset_id": asset_id,
            "path": destination.relative_to(PACK_ROOT / "assets").as_posix(),
            "report_title": clipped(item["report_title"], 300),
            "publisher": clipped(item["publisher"], 200),
            "year": int(item["year"]),
            "source_page": int(item["source_page"]),
            "caption": clipped(item["caption"], 560),
            "usage_scope": "internal-analysis",
            "category": "data_charts",
            "asset_type": "data_chart",
            "provenance": "derived_from_report_data",
            "chart_title": clipped(item["title"], 300),
            "data_path": data_destination.relative_to(PACK_ROOT / "assets").as_posix(),
            "data_notes": clipped(item.get("data_notes", ""), 500),
            "source_documents": [Path(value).name for value in item.get("source_path", [])],
            "sha256": sha256(destination),
        }
        output.append(record)
    return output


def write_selection_report(records: list[dict]) -> None:
    columns = [
        "asset_id", "asset_type", "category", "report_title", "publisher",
        "year", "source_page", "caption", "path", "data_path", "provenance", "sha256",
    ]
    with (PACK_ROOT / "asset_selection_report.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    records = copy_originals() + copy_derived()
    records.sort(key=lambda item: item["asset_id"])
    manifest = {"schema_version": "1.0", "asset_count": len(records), "assets": records}
    manifest_path = PACK_ROOT / "assets" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_selection_report(records)
    counts = Counter(item["category"] for item in records)
    print(json.dumps({"asset_count": len(records), "categories": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
