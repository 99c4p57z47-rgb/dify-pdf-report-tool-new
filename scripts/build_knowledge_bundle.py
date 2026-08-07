#!/usr/bin/env python3
"""Build the lightweight, server-readable home-textile knowledge bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
VISUAL_LABEL = re.compile(
    r"^\s*(?:Visual evidence from source page \d+|Figure crop \d+ from source page \d+):\s*$",
    re.IGNORECASE,
)
YEAR = re.compile(r"20\d{2}")


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~", ""}:
        return ""
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        match = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if match:
            metadata[match.group(1)] = _scalar(match.group(2))
    return metadata, text[end + 5 :]


def _clean_markdown(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if IMAGE_LINE.match(line) or VISUAL_LABEL.match(line):
            continue
        lines.append(line.rstrip())
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"
    return cleaned


def _year(metadata: dict[str, Any], title: str) -> str:
    for key in ("report_year", "year", "report_time", "date", "published_at"):
        matches = YEAR.findall(str(metadata.get(key, "")))
        if matches:
            return max(matches)
    matches = YEAR.findall(title)
    return max(matches) if matches else ""


def _source_id(metadata: dict[str, Any], filename: str) -> str:
    existing = str(metadata.get("document_id", "")).strip()
    if existing:
        return existing
    prefix = re.match(r"^([0-9]{3}[a-z]?|X[0-9a-fA-F]+)", filename)
    if prefix:
        return f"KB-{prefix.group(1).upper()}"
    return "KB-" + hashlib.sha1(filename.encode("utf-8")).hexdigest()[:10].upper()


def _category(metadata: dict[str, Any], title: str) -> str:
    existing = str(metadata.get("primary_category") or "").strip()
    if existing:
        return existing
    if re.search(r"洗护|洗衣|干衣|Laundry|碳足迹|能效", title, re.IGNORECASE):
        return "laundry_technology"
    if re.search(r"年度报告|战略|企业经营|公司经营", title, re.IGNORECASE):
        return "annual_strategy"
    if re.search(r"CIFF|家具|家居|空间|室内|生活生态", title, re.IGNORECASE):
        return "furniture_interior"
    if re.search(r"趋势|色彩|CMF|Intertextile|Heimtextil|流行", title, re.IGNORECASE):
        return "seasonal_trends"
    if re.search(r"行业|市场|运行|出口|进口|零售|消费|618|数据", title, re.IGNORECASE):
        return "home_textile_market"
    return "home_textile_market"


def _report_record(path: Path, metadata: dict[str, Any], cleaned: str) -> dict[str, Any]:
    title = str(metadata.get("title") or path.stem).strip()
    organization = str(metadata.get("publisher") or metadata.get("source") or "待核验").strip()
    content_status = str(metadata.get("content_status") or "").strip()
    is_full = metadata.get("is_full_report")
    if not isinstance(is_full, bool):
        is_full = content_status != "source_card_only"
    if not content_status:
        content_status = "full_local_report" if is_full else "source_card_only"
    category = _category(metadata, title)
    published_at = _year(metadata, title)
    return {
        "source_id": _source_id(metadata, path.name),
        "title": title,
        "organization": organization,
        "published_at": published_at,
        "category": category,
        "content_status": content_status,
        "is_full_report": is_full,
        "is_securities": bool(metadata.get("is_securities", False)),
        "source_url": str(metadata.get("source_url") or "").strip(),
        "report_path": f"reports/{path.name}",
        "characters": len(cleaned),
        "sha256": hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
    }


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    path.write_text(content, encoding="utf-8")


def _readme(report_count: int, asset_count: int) -> str:
    return f"""# 家纺行业报告轻量知识库

本目录供 Railway 上的 PDF 服务读取，不直接存放原始 PDF。

## 内容

- `reports/`：{report_count} 份清洗后的 Markdown 报告或公开来源卡片；
- `catalog.json`：报告与图片的统一机器目录；
- `source_cards.jsonl`：标准化来源字段；
- `asset_catalog.jsonl`：{asset_count} 个精选服务器图片资产；
- 图片实体位于仓库根目录 `assets/`，避免重复占用空间。

## 使用边界

1. `content_status=source_card_only` 的资料不是完整报告，只能用于发现资料和初步背景。
2. 精确数值必须引用对应 `source_id`，不得把目录摘要包装成完整报告结论。
3. `is_securities=true` 的资料不得用于用户要求排除证券研报的任务。
4. PDF服务应从 `asset_catalog.jsonl` 选择相关图片，并从根目录 `assets/` 读取文件。

## 更新

运行：

```bash
python3 scripts/build_knowledge_bundle.py
```

脚本会重新生成整个目录，并验证报告ID、图片ID和文件路径。
"""


def build_knowledge_bundle(
    source_dir: Path,
    asset_manifest: Path,
    output_dir: Path,
    repo_root: Path,
) -> dict[str, int]:
    source_dir = Path(source_dir)
    asset_manifest = Path(asset_manifest)
    output_dir = Path(output_dir)
    repo_root = Path(repo_root)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Markdown source directory not found: {source_dir}")
    if not asset_manifest.is_file():
        raise FileNotFoundError(f"Asset manifest not found: {asset_manifest}")

    staging = output_dir.with_name(output_dir.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    reports_dir = staging / "reports"
    reports_dir.mkdir(parents=True)

    reports: list[dict[str, Any]] = []
    for source in sorted(source_dir.glob("*.md"), key=lambda item: item.name.casefold()):
        raw = source.read_text(encoding="utf-8")
        metadata, body = _frontmatter(raw)
        cleaned_body = _clean_markdown(body)
        normalized_frontmatter = {
            "source_id": _source_id(metadata, source.name),
            "title": str(metadata.get("title") or source.stem),
            "organization": str(metadata.get("publisher") or metadata.get("source") or "待核验"),
            "published_at": _year(metadata, str(metadata.get("title") or source.stem)),
            "category": _category(metadata, str(metadata.get("title") or source.stem)),
            "content_status": str(
                metadata.get("content_status")
                or ("full_local_report" if metadata.get("is_full_report", True) else "source_card_only")
            ),
            "source_url": str(metadata.get("source_url") or ""),
            "is_full_report": bool(metadata.get("is_full_report", metadata.get("content_status") != "source_card_only")),
            "is_securities": bool(metadata.get("is_securities", False)),
        }
        header = "---\n" + "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in normalized_frontmatter.items()
        ) + "\n---\n\n"
        cleaned = header + cleaned_body
        (reports_dir / source.name).write_text(cleaned, encoding="utf-8")
        reports.append(_report_record(source, metadata, cleaned))

    source_ids = [record["source_id"] for record in reports]
    if len(source_ids) != len(set(source_ids)):
        duplicates = sorted({value for value in source_ids if source_ids.count(value) > 1})
        raise ValueError(f"Duplicate source IDs: {duplicates}")

    manifest = json.loads(asset_manifest.read_text(encoding="utf-8"))
    assets: list[dict[str, Any]] = []
    for item in manifest.get("assets", []):
        repo_path = Path("assets") / str(item["path"])
        if not (repo_root / repo_path).is_file():
            raise FileNotFoundError(f"Asset file not found: {repo_path}")
        normalized = dict(item)
        normalized["repo_path"] = repo_path.as_posix()
        assets.append(normalized)
    asset_ids = [str(item["asset_id"]) for item in assets]
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("Duplicate asset IDs in asset manifest")

    _write_jsonl(staging / "source_cards.jsonl", reports)
    _write_jsonl(staging / "asset_catalog.jsonl", assets)
    catalog = {
        "schema_version": "1.0",
        "report_count": len(reports),
        "asset_count": len(assets),
        "reports": reports,
        "assets": assets,
    }
    (staging / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (staging / "README.md").write_text(_readme(len(reports), len(assets)), encoding="utf-8")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    staging.replace(output_dir)
    return {"report_count": len(reports), "asset_count": len(assets)}


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    default_source = repo_root.parent / "行业报告7.29_知识库MD全集"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=default_source)
    parser.add_argument("--asset-manifest", type=Path, default=repo_root / "assets" / "manifest.json")
    parser.add_argument("--output-dir", type=Path, default=repo_root / "knowledge")
    args = parser.parse_args()
    summary = build_knowledge_bundle(args.source_dir, args.asset_manifest, args.output_dir, repo_root)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
