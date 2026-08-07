import json
from pathlib import Path

from app.knowledge import KnowledgeStore
from app.models import FastReportRequest


def _write_store(tmp_path: Path) -> tuple[Path, Path]:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    catalog = {
        "reports": [
            {
                "source_id": "S1",
                "title": "2026中国家纺睡眠消费趋势",
                "organization": "家纺研究机构",
                "published_at": "2026",
                "category": "home_textile_market",
                "content_status": "full_local_report",
                "is_full_report": True,
                "is_securities": False,
                "source_url": "https://example.com/report",
                "report_path": "reports/sleep.md",
            }
        ],
        "assets": [
            {
                "asset_id": "ht_data_charts_001",
                "report_title": "2026中国家纺睡眠消费趋势",
                "publisher": "家纺研究机构",
                "year": 2026,
                "category": "data_charts",
                "asset_type": "data_chart",
                "caption": "睡眠消费市场趋势图",
                "source_page": 4,
                "repo_path": "assets/data_charts/chart.jpg",
            }
        ],
    }
    (knowledge / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    assets = tmp_path / "assets"
    (assets / "data_charts").mkdir(parents=True)
    (assets / "data_charts" / "chart.jpg").write_bytes(b"jpeg")
    return knowledge, assets


def test_fast_request_is_enriched_with_sources_and_server_images(tmp_path: Path) -> None:
    knowledge, assets = _write_store(tmp_path)
    store = KnowledgeStore.from_catalog(knowledge / "catalog.json", assets)
    request = FastReportRequest.model_validate(
        {
            "title": "2026年家纺睡眠消费报告",
            "year": 2026,
            "include_images": True,
            "sections": [
                {
                    "heading": "睡眠消费市场",
                    "body_markdown": "消费者更加关注睡眠产品。",
                    "key_points": ["关注睡眠健康"],
                }
            ],
        }
    )

    report = store.enrich(request)

    assert [source.source_id for source in report.sources] == ["S1"]
    assert report.sections[0].source_ids == ["S1"]
    assert report.sections[0].images[0].asset_id == "ht_data_charts_001"
    assert report.sections[0].images[0].caption == "睡眠消费市场趋势图"


def test_search_excludes_securities_reports(tmp_path: Path) -> None:
    knowledge, assets = _write_store(tmp_path)
    data = json.loads((knowledge / "catalog.json").read_text(encoding="utf-8"))
    data["reports"].append(
        {
            "source_id": "SEC1",
            "title": "家纺睡眠证券研报",
            "organization": "某证券",
            "published_at": "2026",
            "category": "home_textile_market",
            "content_status": "full_local_report",
            "is_full_report": True,
            "is_securities": True,
            "source_url": "",
            "report_path": "reports/sec.md",
        }
    )
    (knowledge / "catalog.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    store = KnowledgeStore.from_catalog(knowledge / "catalog.json", assets)

    assert all(item["source_id"] != "SEC1" for item in store.search("家纺睡眠", limit=10))


def test_fast_request_does_not_repeat_the_same_image_across_sections(tmp_path: Path) -> None:
    knowledge, assets = _write_store(tmp_path)
    data = json.loads((knowledge / "catalog.json").read_text(encoding="utf-8"))
    second = dict(data["assets"][0])
    second["asset_id"] = "ht_data_charts_002"
    second["repo_path"] = "assets/data_charts/chart-2.jpg"
    data["assets"].append(second)
    (knowledge / "catalog.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (assets / "data_charts" / "chart-2.jpg").write_bytes(b"jpeg")
    store = KnowledgeStore.from_catalog(knowledge / "catalog.json", assets)
    request = FastReportRequest.model_validate(
        {
            "title": "家纺睡眠报告",
            "sections": [
                {"heading": "睡眠消费市场"},
                {"heading": "睡眠消费机会"},
            ],
        }
    )

    report = store.enrich(request)
    image_ids = [section.images[0].asset_id for section in report.sections]
    assert len(image_ids) == len(set(image_ids)) == 2
