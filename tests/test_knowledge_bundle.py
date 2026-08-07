import json
from pathlib import Path

from scripts.build_knowledge_bundle import build_knowledge_bundle


def test_builds_normalized_reports_and_indexes_assets(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "001_demo.md").write_text(
        """---
document_id: "HTMM-001"
title: "2026家纺示例报告"
publisher: "示例机构"
report_year: 2026
primary_category: "home_textile_market"
is_securities: false
---

# 2026家纺示例报告

有效正文。

Visual evidence from source page 1:

![source page](../assets/001/visual_pages/p001_page.jpg)
""",
        encoding="utf-8",
    )

    repo_root = tmp_path / "repo"
    image_path = repo_root / "assets" / "data_charts" / "chart.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"jpeg")
    asset_manifest = repo_root / "assets" / "manifest.json"
    asset_manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "asset_count": 1,
                "assets": [
                    {
                        "asset_id": "ht_data_charts_001",
                        "path": "data_charts/chart.jpg",
                        "report_title": "2026家纺示例报告",
                        "publisher": "示例机构",
                        "year": 2026,
                        "category": "data_charts",
                        "asset_type": "data_chart",
                        "caption": "市场数据图表",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    output_dir = repo_root / "knowledge"
    summary = build_knowledge_bundle(source_dir, asset_manifest, output_dir, repo_root)

    assert summary["report_count"] == 1
    assert summary["asset_count"] == 1
    report = (output_dir / "reports" / "001_demo.md").read_text(encoding="utf-8")
    assert "有效正文" in report
    assert "![source page]" not in report
    assert "Visual evidence from source page" not in report

    catalog = json.loads((output_dir / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["reports"][0]["source_id"] == "HTMM-001"
    assert catalog["reports"][0]["organization"] == "示例机构"
    assert catalog["assets"][0]["repo_path"] == "assets/data_charts/chart.jpg"

    cards = [
        json.loads(line)
        for line in (output_dir / "source_cards.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert cards[0]["published_at"] == "2026"
    assert cards[0]["content_status"] == "full_local_report"


def test_marks_online_source_cards_as_non_full_reports(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "029_online.md").write_text(
        """---
document_id: "online-029"
title: "公开目录资料"
publisher: "公开平台"
report_time: "2026年6月"
primary_category: "home_textile_market"
content_status: "source_card_only"
source_url: "https://example.com/report"
is_full_report: false
is_securities: false
---

# 公开目录资料

仅用于发现资料。
""",
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    manifest = repo_root / "assets" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"asset_count": 0, "assets": []}', encoding="utf-8")

    build_knowledge_bundle(source_dir, manifest, repo_root / "knowledge", repo_root)
    card = json.loads(
        (repo_root / "knowledge" / "source_cards.jsonl").read_text(encoding="utf-8").strip()
    )
    assert card["content_status"] == "source_card_only"
    assert card["is_full_report"] is False
    assert card["source_url"] == "https://example.com/report"


def test_infers_category_when_legacy_metadata_has_no_category(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "121_market.md").write_text(
        """---
title: "2024年我国家纺行业运行分析"
publisher: "中国家用纺织品行业协会"
is_securities: false
---

# 2024年我国家纺行业运行分析
""",
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    manifest = repo_root / "assets" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"asset_count": 0, "assets": []}', encoding="utf-8")

    build_knowledge_bundle(source_dir, manifest, repo_root / "knowledge", repo_root)
    catalog = json.loads((repo_root / "knowledge" / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["reports"][0]["category"] == "home_textile_market"
