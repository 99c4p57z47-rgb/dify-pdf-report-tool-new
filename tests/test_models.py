import pytest
from pydantic import ValidationError

from app.models import ImageSpec, ReportRequest, SourceSpec


def test_image_requires_exactly_one_locator():
    with pytest.raises(ValidationError):
        ImageSpec(caption="图", asset_id="a", url="https://example.com/a.png")
    with pytest.raises(ValidationError):
        ImageSpec(caption="图")


def test_image_rejects_local_paths():
    for value in ("../assets/a.png", "file:///tmp/a.png", "/tmp/a.png"):
        with pytest.raises(ValidationError):
            ImageSpec(caption="图", url=value)


def test_image_and_source_metadata_urls_require_https():
    with pytest.raises(ValidationError, match="HTTPS"):
        ImageSpec(caption="图", url="http://example.com/image.png")
    with pytest.raises(ValidationError, match="HTTPS"):
        ImageSpec(caption="图", url="https://example.com/image.png", source_url="http://example.com/report")


def test_report_rejects_placeholders():
    with pytest.raises(ValidationError, match="占位内容"):
        ReportRequest(title="测试", sections=[{"heading": "市场", "body_markdown": "执行 GB/T XXXX-2026"}])


def test_report_response_has_quality_fields():
    from app.models import ReportResponse

    fields = ReportResponse.model_fields
    assert {"download_url", "page_count", "image_count", "warnings", "quality_check"} <= fields.keys()


def test_numeric_section_requires_existing_source_id():
    with pytest.raises(ValidationError, match="source_ids"):
        ReportRequest(
            title="测试",
            sections=[{"heading": "市场", "body_markdown": "市场增长 12.5%"}],
            sources=[
                {
                    "source_id": "s1",
                    "title": "行业报告",
                    "organization": "行业协会",
                    "published_at": "2026-08-04",
                }
            ],
        )


def test_unknown_source_reference_is_rejected():
    with pytest.raises(ValidationError, match="不存在"):
        ReportRequest(
            title="测试",
            sections=[{"heading": "市场", "body_markdown": "市场增长 12.5%", "source_ids": ["missing"]}],
            sources=[
                {
                    "source_id": "s1",
                    "title": "行业报告",
                    "organization": "行业协会",
                    "published_at": "2026-08-04",
                }
            ],
        )


def test_chart_data_requires_source_ids():
    with pytest.raises(ValidationError, match="source_ids"):
        ReportRequest(
            title="测试",
            sections=[
                {
                    "heading": "市场",
                    "charts": [
                        {
                            "type": "bar",
                            "title": "市场规模",
                            "labels": ["2025", "2026"],
                            "datasets": [{"label": "规模", "data": [10.0, 12.5]}],
                        }
                    ],
                }
            ],
            sources=[
                {
                    "source_id": "s1",
                    "title": "行业报告",
                    "organization": "行业协会",
                    "published_at": "2026-08-04",
                }
            ],
        )


def test_chart_source_ids_must_exist():
    with pytest.raises(ValidationError, match="不存在"):
        ReportRequest(
            title="测试",
            sections=[
                {
                    "heading": "市场",
                    "charts": [
                        {
                            "type": "bar",
                            "title": "市场规模",
                            "labels": ["2025", "2026"],
                            "datasets": [{"label": "规模", "data": [10.0, 12.5]}],
                            "source_ids": ["missing"],
                        }
                    ],
                }
            ],
            sources=[
                {
                    "source_id": "s1",
                    "title": "行业报告",
                    "organization": "行业协会",
                    "published_at": "2026-08-04",
                }
            ],
        )


def test_asset_id_rejects_paths_and_non_id_syntax():
    for value in ("../assets/a.png", "report/../a", "/tmp/a.png", "file:///tmp/a.png", "asset://report_1"):
        with pytest.raises(ValidationError):
            ImageSpec(caption="图", asset_id=value)


def test_source_requires_complete_metadata_and_https_url():
    with pytest.raises(ValidationError):
        SourceSpec(source_id="s1", title="行业报告", published_at="2026-08-04")
    with pytest.raises(ValidationError):
        SourceSpec(source_id="s1", title="行业报告", organization="行业协会")
    with pytest.raises(ValidationError):
        SourceSpec(
            source_id="s1",
            title="行业报告",
            organization="行业协会",
            published_at="2026-08-04",
            url="http://example.com/report",
        )


def test_source_rejects_whitespace_only_organization():
    with pytest.raises(ValidationError):
        SourceSpec(
            source_id="s1",
            title="行业报告",
            organization="   ",
            published_at="2026-08-04",
        )


def test_source_rejects_whitespace_only_published_at():
    with pytest.raises(ValidationError):
        SourceSpec(
            source_id="s1",
            title="行业报告",
            organization="行业协会",
            published_at="\t\n",
        )
