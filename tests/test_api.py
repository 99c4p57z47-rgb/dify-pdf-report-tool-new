from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from app import main
from app.assets import AssetRegistry, ResolvedImage
from app.layout import BuildResult
from app.models import ReportRequest


def _request_payload(*, include_image: bool = True) -> dict[str, object]:
    section: dict[str, object] = {
        "heading": "市场概览",
        "body_markdown": "这是用于接口质量检查的中文段落，不包含未经引用的精确数值。",
    }
    if include_image:
        section["images"] = [
            {"asset_id": "unknown_asset", "caption": "可选知识库图片"}
        ]
    return {"title": "接口编排测试报告", "sections": [section]}


def _write_payload_pdf(path: Path, payload: ReportRequest) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    document = canvas.Canvas(str(path))
    document.setTitle(payload.title)
    document.setAuthor("Codex")
    document.setFont("STSong-Light", 12)
    lines = [payload.title]
    for section in payload.sections:
        lines.extend(
            [
                section.heading,
                section.summary,
                *section.key_points,
                section.body_markdown,
            ]
        )
    for index, line in enumerate(filter(None, lines)):
        document.drawString(72, 740 - index * 24, line[:100])
    document.save()


@pytest.fixture
def ready_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    missing = object()
    previous_registry = getattr(main.app.state, "font_registry", missing)
    previous_error = getattr(main.app.state, "font_error", missing)
    asset_root = tmp_path / "assets"
    asset_root.mkdir()

    monkeypatch.setattr(main, "API_TOKEN", "test-token")
    monkeypatch.setattr(main, "ASSET_REGISTRY_ERROR", "")
    monkeypatch.setattr(main, "ASSET_REGISTRY", AssetRegistry.empty(asset_root))
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path / "output")
    main.OUTPUT_DIR.mkdir()
    main.app.state.font_registry = object()
    main.app.state.font_error = None

    try:
        yield TestClient(main.app)
    finally:
        if previous_registry is missing:
            main.app.state._state.pop("font_registry", None)
        else:
            main.app.state.font_registry = previous_registry
        if previous_error is missing:
            main.app.state._state.pop("font_error", None)
        else:
            main.app.state.font_error = previous_error


def _stub_successful_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_render_report_charts", lambda *args: ({}, []))

    def build(
        payload: ReportRequest,
        output_path: Path,
        image_paths: dict[tuple[int, int], ResolvedImage],
        chart_paths: dict[tuple[int, int], Path],
    ) -> BuildResult:
        _write_payload_pdf(output_path, payload)
        return BuildResult(page_count=1, image_count=0)

    monkeypatch.setattr(main, "_build_report_pdf", build)
    monkeypatch.setattr(
        main,
        "render_pages",
        lambda pdf_path, output_dir, dpi=120: [output_dir / "page-1.png"],
    )


def test_unknown_asset_degrades_to_successful_report(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _stub_successful_render(monkeypatch)

    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json=_request_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["page_count"] == 1
    assert body["image_count"] == 0
    assert len(body["warnings"]) == 1
    assert "unknown_asset" in body["warnings"][0]
    assert body["quality_check"] == "passed_with_warnings"
    assert body["download_url"].endswith(quote(body["filename"], safe=""))


def test_report_endpoint_preserves_bearer_authentication(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _stub_successful_render(monkeypatch)

    response = ready_api.post("/v1/reports", json=_request_payload(include_image=False))

    assert response.status_code == 401


def test_validation_error_names_exact_field_path(ready_api: TestClient):
    payload = _request_payload(include_image=False)
    payload["sections"] = [{"body_markdown": "缺少标题"}]

    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json=payload,
    )

    assert response.status_code == 422
    assert "sections.0.heading" in json.dumps(response.json(), ensure_ascii=False)


def test_model_validator_error_uses_explicit_field_path(ready_api: TestClient):
    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "来源路径测试",
            "sections": [
                {
                    "heading": "市场",
                    "body_markdown": "市场增长 12.5%",
                }
            ],
            "sources": [
                {
                    "source_id": "s1",
                    "title": "行业报告",
                    "organization": "行业协会",
                    "published_at": "2026-08-05",
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "sections.0.source_ids"


def test_image_resolution_concurrency_never_exceeds_four(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    first_payload = ReportRequest.model_validate(
        {
            "title": "并发边界测试一",
            "sections": [
                {
                    "heading": "图片",
                    "images": [
                        {"asset_id": f"asset_{index}", "caption": f"图片 {index}"}
                        for index in range(4)
                    ],
                }
            ],
        }
    )
    second_payload = ReportRequest.model_validate(
        {
            "title": "并发边界测试二",
            "sections": [
                {
                    "heading": "图片",
                    "images": [
                        {
                            "asset_id": f"asset_{index + 4}",
                            "caption": f"图片 {index + 4}",
                        }
                        for index in range(4)
                    ],
                }
            ],
        }
    )

    async def scenario():
        active = 0
        peak = 0
        release = asyncio.Event()
        work_dirs: set[Path] = set()
        monkeypatch.setattr(
            main,
            "_IMAGE_RESOLUTION_LIMITER",
            threading.BoundedSemaphore(4),
        )

        async def fake_resolve(spec, registry, client, work_dir):
            nonlocal active, peak
            work_dirs.add(work_dir)
            active += 1
            peak = max(peak, active)
            if peak == 4:
                release.set()
            await release.wait()
            await asyncio.sleep(0)
            active -= 1
            return ResolvedImage(
                path=work_dir / "image.jpg",
                caption=spec.caption,
                report_title=None,
                publisher=None,
                year=None,
                source_page=None,
            )

        monkeypatch.setattr(main, "resolve_image", fake_resolve)
        results = await asyncio.wait_for(
            asyncio.gather(
                main._resolve_images_concurrently(
                    first_payload,
                    object(),
                    tmp_path / "request-one",
                ),
                main._resolve_images_concurrently(
                    second_payload,
                    object(),
                    tmp_path / "request-two",
                ),
            ),
            timeout=2,
        )
        return peak, work_dirs, results

    peak, work_dirs, results = asyncio.run(scenario())

    images = [image for image_map, _ in results for image in image_map.values()]
    warnings = [warning for _, warning_list in results for warning in warning_list]
    assert peak == 4
    assert len(images) == 8
    assert len(work_dirs) == 8
    assert warnings == []


def test_image_resolution_limit_is_shared_across_event_loops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Separate TestClient portals/event loops must share one process cap."""
    payloads = [
        ReportRequest.model_validate(
            {
                "title": f"Cross-loop report {request_index}",
                "sections": [
                    {
                        "heading": "Images",
                        "images": [
                            {
                                "asset_id": f"asset_{request_index}_{image_index}",
                                "caption": f"Image {request_index}-{image_index}",
                            }
                            for image_index in range(4)
                        ],
                    }
                ],
            }
        )
        for request_index in range(2)
    ]
    monkeypatch.setattr(
        main,
        "_IMAGE_RESOLUTION_LIMITER",
        threading.BoundedSemaphore(4),
    )
    active = 0
    peak = 0
    lock = threading.Lock()
    start = threading.Barrier(2)

    async def fake_resolve(spec, registry, client, work_dir):
        nonlocal active, peak
        del registry, client
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            await asyncio.sleep(0.04)
        finally:
            with lock:
                active -= 1
        return ResolvedImage(
            path=work_dir / "image.jpg",
            caption=spec.caption,
            report_title=None,
            publisher=None,
            year=None,
            source_page=None,
        )

    monkeypatch.setattr(main, "resolve_image", fake_resolve)

    def run_in_portal(request_index: int):
        start.wait(timeout=2)
        return asyncio.run(
            asyncio.wait_for(
                main._resolve_images_concurrently(
                    payloads[request_index],
                    object(),
                    tmp_path / f"portal-{request_index}",
                ),
                timeout=3,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_in_portal, index) for index in range(2)]
        results = [future.result(timeout=4) for future in futures]

    assert peak == 4
    assert sum(len(images) for images, _ in results) == 8
    assert [warning for _, warnings in results for warning in warnings] == []


def test_image_limiter_failure_is_not_downgraded_to_optional_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    payload = ReportRequest.model_validate(_request_payload())

    class BrokenLimiter:
        def acquire(self):
            raise RuntimeError("process image limiter failed")

        def release(self):
            raise AssertionError("an unacquired permit must not be released")

    monkeypatch.setattr(main, "_IMAGE_RESOLUTION_LIMITER", BrokenLimiter())

    with pytest.raises(RuntimeError, match="process image limiter failed"):
        asyncio.run(
            main._resolve_images_concurrently(
                payload,
                object(),
                tmp_path / "limiter-failure",
            )
        )


def test_markers_cover_previously_omitted_laid_out_fields(tmp_path: Path):
    payload = ReportRequest.model_validate(
        {
            "title": "English report",
            "subtitle": "副标题中文",
            "author": "显式作者中文",
            "generated_at": "生成日期中文",
            "executive_summary": "English summary",
            "executive_insights": [
                {
                    "claim": "English claim",
                    "evidence": "证据基础中文",
                    "implication": "行动建议中文",
                }
            ],
            "sections": [
                {
                    "heading": "English section",
                    "summary": "章节摘要中文",
                    "key_points": ["关键发现中文"],
                    "body_markdown": "English body",
                    "images": [
                        {
                            "url": "https://example.com/image.png",
                            "caption": "请求图注不应替代实际图注",
                        }
                    ],
                    "charts": [
                        {
                            "type": "bar",
                            "title": "图表标题中文",
                            "labels": ["渠道标签甲", "渠道标签乙"],
                            "datasets": [
                                {"label": "数据系列中文", "data": [1, 2]}
                            ],
                            "unit": "金额单位中文",
                            "source": "图表来源中文",
                            "note": "图表备注中文",
                            "source_ids": ["source-1"],
                        }
                    ],
                    "source_ids": ["source-1"],
                }
            ],
            "methodology": "方法口径中文",
            "disclaimer": "显式声明中文",
            "sources": [
                {
                    "source_id": "source-1",
                    "title": "来源标题中文",
                    "organization": "来源机构中文",
                    "published_at": "发布日期中文",
                    "data_period": "数据期间中文",
                    "source_type": "来源类型中文",
                    "url": "https://example.com/source",
                }
            ],
        }
    )
    resolved = ResolvedImage(
        path=tmp_path / "resolved.png",
        caption="实际图片图注",
        report_title="图片来源标题",
        publisher="图片出版机构",
        year=2026,
        source_page=8,
    )

    markers = main._required_chinese_markers(
        payload,
        image_paths={(0, 0): resolved},
        rendered_image_keys=((0, 0),),
        rendered_chart_keys=((0, 0),),
    )

    expected = {
        "副标题中文",
        "显式作者中文",
        "生成日期中文",
        "证据基础中文",
        "行动建议中文",
        "章节摘要中文",
        "关键发现中文",
        "方法口径中文",
        "显式声明中文",
        "来源标题中文",
        "来源机构中文",
        "发布日期中文",
        "数据期间中文",
        "来源类型中文",
        "图表标题中文",
        "渠道标签甲",
        "渠道标签乙",
        "数据系列中文",
        "金额单位中文",
        "图表来源中文",
        "图表备注中文",
        "实际图片图注",
        "图片来源标题",
        "图片出版机构",
    }
    assert expected.issubset(set(markers))
    assert not any("本报告基于已注明" in marker for marker in markers)

    omitted_pdf = tmp_path / "omitted-fields.pdf"
    document = canvas.Canvas(str(omitted_pdf))
    document.setTitle("English report")
    document.setFont("Helvetica", 12)
    document.drawString(
        72,
        720,
        "English-only output with enough text but none of the payload Chinese.",
    )
    document.save()

    summary = main.inspect_pdf(
        omitted_pdf,
        expected_images=0,
        required_chinese_markers=markers,
    )
    assert summary.status == "failed"
    assert any("副标题中文" in error for error in summary.errors)


def test_markdown_markers_ignore_non_visible_https_link_targets():
    payload = ReportRequest.model_validate(
        {
            "title": "English report",
            "executive_summary": (
                "[摘要可见标签](https://中文摘要域名.example/中文摘要路径)"
            ),
            "sections": [
                {
                    "heading": "English section",
                    "body_markdown": (
                        "[正文可见标签](https://中文正文域名.example/中文正文路径)"
                    ),
                }
            ],
            "methodology": (
                "[方法可见标签](https://中文方法域名.example/中文方法路径)"
            ),
        }
    )

    markers = set(main._required_chinese_markers(payload))

    assert {"摘要可见标签", "正文可见标签", "方法可见标签"} <= markers
    assert {
        "中文摘要域名",
        "中文摘要路径",
        "中文正文域名",
        "中文正文路径",
        "中文方法域名",
        "中文方法路径",
    }.isdisjoint(markers)


def test_marker_anchors_dedupe_within_field_but_preserve_field_count():
    repeated_anchor = "甲乙丙丁戊己庚辛壬癸"
    field_value = f"{repeated_anchor}中间{repeated_anchor}"
    payload = ReportRequest.model_validate(
        {
            "title": field_value,
            "sections": [{"heading": field_value}],
        }
    )

    assert main._markers_from_text(field_value) == (repeated_anchor,)
    assert main._required_chinese_markers(payload).count(repeated_anchor) == 2


def test_chart_markers_match_bounded_visible_label_summary():
    payload = ReportRequest.model_validate(
        {
            "title": "English report",
            "sections": [
                {
                    "heading": "English section",
                    "charts": [
                        {
                            "type": "bar",
                            "title": "English chart",
                            "labels": [
                                "第一渠道标签用于测试非常长的可见辅助文本尾端不得要求",
                                "第二渠道标签用于测试非常长的可见辅助文本尾端不得要求",
                            ],
                            "datasets": [
                                {
                                    "label": "第一数据系列用于测试非常长的图例文本尾端不得要求",
                                    "data": [1, 2],
                                }
                            ],
                            "source": "English source",
                            "source_ids": ["source-1"],
                        }
                    ],
                    "source_ids": ["source-1"],
                }
            ],
            "sources": [
                {
                    "source_id": "source-1",
                    "title": "English source title",
                    "organization": "English organization",
                    "published_at": "2026",
                }
            ],
        }
    )

    markers = set(
        main._required_chinese_markers(
            payload,
            rendered_chart_keys=((0, 0),),
        )
    )

    assert {
        "第一渠道标签用于",
        "第二渠道标签用于",
        "第一数据系列用于",
    } <= markers
    assert "文本尾端不得要求" not in markers


def test_temporary_directory_is_cleaned_when_build_fails(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def tracked_mkdtemp(*args, **kwargs) -> str:
        kwargs["dir"] = tmp_path
        path = Path(real_mkdtemp(*args, **kwargs))
        created.append(path)
        return str(path)

    monkeypatch.setattr(main.tempfile, "mkdtemp", tracked_mkdtemp)
    monkeypatch.setattr(main, "_render_report_charts", lambda *args: ({}, []))

    secret = str(tmp_path / "internal" / "secret.pdf")

    def fail_build(
        payload: ReportRequest,
        output_path: Path,
        image_paths: dict[tuple[int, int], ResolvedImage],
        chart_paths: dict[tuple[int, int], Path],
    ):
        output_path.write_bytes(b"%PDF-partial")
        raise RuntimeError(f"core build failed at {secret}")

    monkeypatch.setattr(main, "_build_report_pdf", fail_build)
    caplog.set_level(logging.ERROR)

    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json=_request_payload(include_image=False),
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    report_id = re.search(r"report_id=([0-9a-f]{16})", detail)
    assert report_id is not None
    assert "core build failed" not in detail
    assert secret not in detail
    assert report_id.group(1) in caplog.text
    assert "core build failed" in caplog.text
    assert list(main.OUTPUT_DIR.iterdir()) == []
    assert created
    assert all(not path.exists() for path in created)


def test_cancellation_waits_for_builder_before_cleaning_files(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def tracked_mkdtemp(*args, **kwargs) -> str:
        kwargs["dir"] = tmp_path
        path = Path(real_mkdtemp(*args, **kwargs))
        created.append(path)
        return str(path)

    def blocking_build(
        payload: ReportRequest,
        output_path: Path,
        image_paths: dict[tuple[int, int], ResolvedImage],
        chart_paths: dict[tuple[int, int], Path],
    ) -> BuildResult:
        output_path.write_bytes(b"%PDF-partial")
        started.set()
        try:
            if not release.wait(timeout=3):
                raise RuntimeError("builder release timed out")
            return BuildResult(page_count=1, image_count=0)
        finally:
            finished.set()

    monkeypatch.setattr(main.tempfile, "mkdtemp", tracked_mkdtemp)
    monkeypatch.setattr(main, "_render_report_charts", lambda *args: ({}, []))
    monkeypatch.setattr(main, "_build_report_pdf", blocking_build)
    payload = ReportRequest.model_validate(_request_payload(include_image=False))

    async def scenario() -> bool:
        task = asyncio.create_task(
            main.create_report(payload, SimpleNamespace(base_url="http://testserver/"))
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=2)
        task.cancel()
        await asyncio.sleep(0.05)
        waiting_for_builder = not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        await asyncio.wait_for(asyncio.to_thread(finished.wait), timeout=2)
        return waiting_for_builder

    try:
        assert asyncio.run(scenario()) is True
    finally:
        release.set()

    assert list(main.OUTPUT_DIR.iterdir()) == []
    assert created
    assert all(not path.exists() for path in created)


def test_pdf_without_chinese_text_is_not_published(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(main, "_render_report_charts", lambda *args: ({}, []))

    def build_blank(
        payload: ReportRequest,
        output_path: Path,
        image_paths: dict[tuple[int, int], ResolvedImage],
        chart_paths: dict[tuple[int, int], Path],
    ) -> BuildResult:
        document = canvas.Canvas(str(output_path))
        document.setTitle("Fixed template")
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        document.setFont("STSong-Light", 12)
        document.drawString(72, 720, "行业研究数据洞察趋势研判固定模板文字")
        document.save()
        return BuildResult(page_count=1, image_count=0)

    monkeypatch.setattr(main, "_build_report_pdf", build_blank)

    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json=_request_payload(include_image=False),
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert re.search(r"report_id=[0-9a-f]{16}", detail)
    assert "接口编排测试报告" not in detail
    assert "市场概览" not in detail


def test_english_only_payload_can_pass_quality_gate(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _stub_successful_render(monkeypatch)
    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "English Market Report",
            "sections": [
                {
                    "heading": "Overview",
                    "body_markdown": (
                        "This English-only section contains enough extractable text "
                        "for the structural quality gate."
                    ),
                }
            ],
        },
    )

    assert response.status_code == 200


def test_poppler_stderr_is_not_exposed_to_client(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _stub_successful_render(monkeypatch)
    secret = "/private/internal/report.pdf"
    monkeypatch.setattr(
        main,
        "render_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            main.PdfRenderError(f"Unknown font tag at {secret}")
        ),
    )

    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json=_request_payload(include_image=False),
    )

    assert response.status_code == 500
    assert "Unknown font tag" not in response.json()["detail"]
    assert secret not in response.json()["detail"]


def test_download_url_percent_encodes_filename(
    ready_api: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _stub_successful_render(monkeypatch)
    payload = _request_payload(include_image=False)
    payload["title"] = "中文#报告"

    response = ready_api.post(
        "/v1/reports",
        headers={"Authorization": "Bearer test-token"},
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["download_url"].endswith(quote(body["filename"], safe=""))
    assert "#" not in body["download_url"]
    assert "中文" not in body["download_url"]
