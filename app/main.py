from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.assets import (
    AssetManifestError,
    AssetRegistry,
    ResolvedImage,
    resolve_image,
)
from app.charts import DEFAULT_PALETTE, ChartDataError, render_chart, validate_chart
from app.fonts import FontSetupError, register_fonts, verify_font_probe
from app.layout import (
    BuildResult,
    ReportTheme,
    StrategicReportBuilder,
    chart_accessible_labels,
)
from app.markdown import visible_markdown_text
from app.models import ImageSpec, ReportRequest, ReportResponse
from app.quality import inspect_pdf
from scripts.render_pdf_pages import PdfRenderError, render_pages


logger = logging.getLogger(__name__)
APP_NAME = "Dify Industry PDF Report Tool"
OUTPUT_DIR = Path(os.getenv("PDF_OUTPUT_DIR", "./output")).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
API_TOKEN = os.getenv("PDF_TOOL_API_TOKEN", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
MAX_REPORT_SECTIONS = int(os.getenv("MAX_REPORT_SECTIONS", "20"))
MAX_REPORT_IMAGES = int(os.getenv("MAX_REPORT_IMAGES", "30"))
ASSET_DIR = Path(os.getenv("PDF_ASSET_DIR", "./assets")).resolve()
ASSET_MANIFEST = ASSET_DIR / "manifest.json"
ENABLE_ASSET_PREVIEW = os.getenv("ENABLE_ASSET_PREVIEW", "").strip().lower() == "true"
_VALIDATOR_FIELD_PATH = re.compile(
    r"\b((?:sections|executive_insights)(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|\d+))+)[：:]"
)
_CHINESE_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_CHINESE_MARKER_WIDTH = 10
_IMAGE_RESOLUTION_LIMITER = threading.BoundedSemaphore(4)
try:
    ASSET_REGISTRY = AssetRegistry.from_manifest(ASSET_DIR, ASSET_MANIFEST)
    ASSET_REGISTRY_ERROR = ""
except AssetManifestError as exc:
    ASSET_REGISTRY = AssetRegistry.empty(ASSET_DIR)
    ASSET_REGISTRY_ERROR = str(exc)

app = FastAPI(
    title=APP_NAME,
    version="1.0.0",
    description="Render a source-attributed Chinese industry report with images and charts, then return a PDF download URL.",
)
app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")
if ENABLE_ASSET_PREVIEW:
    app.mount("/assets", StaticFiles(directory=str(ASSET_DIR), check_dir=False), name="assets")


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Return stable dot-separated request paths for Dify repair loops."""
    del request
    details: list[dict[str, str]] = []
    for error in exc.errors():
        location = list(error.get("loc", ()))
        if location and location[0] == "body":
            location = location[1:]
        message = str(error.get("msg", "Invalid value"))
        explicit_path = _VALIDATOR_FIELD_PATH.search(message)
        field = ".".join(str(part) for part in location) or "body"
        if field == "body" and explicit_path is not None:
            field = explicit_path.group(1)
        details.append(
            {
                "field": field,
                "message": message,
                "type": str(error.get("type", "value_error")),
            }
        )
    return JSONResponse(status_code=422, content={"detail": details})


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not API_TOKEN:
        return
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def _safe_filename(value: str, fallback: str = "industry-report") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    value = re.sub(r"\s+", "_", value)
    return (value[:120] or fallback) + ".pdf"


def _configure_fonts() -> None:
    """Register and validate the image-packaged fonts, keeping readiness state."""
    try:
        registry = register_fonts(Path(os.environ["CJK_FONT_DIR"]))
        verify_font_probe(registry, Path(tempfile.gettempdir()))
    except (FontSetupError, KeyError) as exc:
        app.state.font_registry = None
        app.state.font_error = str(exc)
        return

    app.state.font_registry = registry
    app.state.font_error = None


@app.on_event("startup")
async def configure_fonts_on_startup() -> None:
    _configure_fonts()


@asynccontextmanager
async def _image_resolution_slot():
    """Hold one process-wide, event-loop-neutral image-resolution permit."""
    limiter = _IMAGE_RESOLUTION_LIMITER
    acquire_task = asyncio.create_task(asyncio.to_thread(limiter.acquire))
    try:
        acquired = await asyncio.shield(acquire_task)
    except asyncio.CancelledError:
        # A worker thread cannot be cancelled once acquire() has started. Wait
        # until ownership is known, then return any acquired permit before the
        # request cancellation is allowed to unwind its temporary directory.
        while not acquire_task.done():
            try:
                await asyncio.shield(acquire_task)
            except asyncio.CancelledError:
                continue
        try:
            acquired_after_cancellation = acquire_task.result()
        except Exception:
            logger.exception("Image limiter acquisition failed after cancellation")
        else:
            if acquired_after_cancellation:
                limiter.release()
        raise

    if not acquired:
        raise RuntimeError("Process image limiter refused a blocking acquisition")
    try:
        yield
    finally:
        limiter.release()


async def _run_blocking(function, *args):
    """Wait for an already-started thread even when the request is cancelled."""
    thread_task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(thread_task)
    except asyncio.CancelledError:
        while not thread_task.done():
            try:
                await asyncio.shield(thread_task)
            except asyncio.CancelledError:
                continue
        try:
            thread_task.result()
        except Exception:
            logger.exception(
                "Blocking report task failed after cancellation task=%s",
                getattr(function, "__name__", type(function).__name__),
            )
        raise


def _markers_from_text(value: str) -> tuple[str, ...]:
    """Return field-local CJK anchors instead of one concatenated sentinel."""
    markers: list[str] = []
    for run in _CHINESE_RUN.findall(value or ""):
        if len(run) <= _CHINESE_MARKER_WIDTH:
            markers.append(run)
            continue
        markers.append(run[:_CHINESE_MARKER_WIDTH])
        markers.append(run[-_CHINESE_MARKER_WIDTH:])
    return tuple(dict.fromkeys(markers))


def _marker_from_text(value: str) -> str:
    """Retain the earlier helper for callers while using field-local anchors."""
    markers = _markers_from_text(value)
    return markers[0] if markers else ""


def _required_chinese_markers(
    payload: ReportRequest,
    *,
    image_paths: Mapping[tuple[int, int], ResolvedImage] | None = None,
    rendered_image_keys: Sequence[tuple[int, int]] = (),
    rendered_chart_keys: Sequence[tuple[int, int]] = (),
) -> tuple[str, ...]:
    """Derive occurrence-sensitive markers from text actually laid out."""
    candidate_texts: list[str] = [
        payload.title,
        payload.subtitle,
        visible_markdown_text(payload.executive_summary),
    ]
    explicitly_supplied = payload.model_fields_set
    if "author" in explicitly_supplied:
        candidate_texts.append(payload.author)
    if "generated_at" in explicitly_supplied:
        candidate_texts.append(payload.generated_at)

    for insight in payload.executive_insights:
        candidate_texts.extend(
            [insight.claim, insight.evidence, insight.implication]
        )

    rendered_images = set(rendered_image_keys)
    rendered_charts = set(rendered_chart_keys)
    resolved_images = image_paths or {}
    for section_index, section in enumerate(payload.sections):
        candidate_texts.extend(
            [
                section.heading,
                section.summary,
                *section.key_points,
                visible_markdown_text(section.body_markdown),
            ]
        )
        for image_index, _image_spec in enumerate(section.images):
            key = (section_index, image_index)
            if key not in rendered_images:
                continue
            resolved = resolved_images.get(key)
            if resolved is not None:
                candidate_texts.extend(
                    [
                        resolved.caption,
                        resolved.report_title or "",
                        resolved.publisher or "",
                    ]
                )
        for chart_index, chart in enumerate(section.charts):
            if (section_index, chart_index) not in rendered_charts:
                continue
            category_labels, dataset_labels = chart_accessible_labels(chart)
            candidate_texts.extend(
                [
                    chart.title,
                    *category_labels,
                    *dataset_labels,
                    chart.unit,
                    chart.source,
                    chart.note,
                ]
            )

    candidate_texts.append(visible_markdown_text(payload.methodology))
    for source in payload.sources:
        candidate_texts.extend(
            [
                source.title,
                source.organization,
                source.published_at,
                source.data_period,
                source.source_type,
            ]
        )
    if "disclaimer" in explicitly_supplied:
        candidate_texts.append(payload.disclaimer)

    return tuple(
        marker
        for text in candidate_texts
        for marker in _markers_from_text(text)
    )


def _build_pdf(
    payload: ReportRequest,
    output_path: Path,
    image_paths: dict[tuple[int, int], ResolvedImage],
    warnings: list[str],
    temp_dir: Path,
) -> int:
    """Backward-compatible Task 5 helper used by local layout probes."""
    chart_paths, chart_warnings = _render_report_charts(payload, temp_dir)
    warnings.extend(chart_warnings)
    result = _build_report_pdf(payload, output_path, image_paths, chart_paths)
    warnings.extend(result.warnings)
    return result.page_count


def _render_report_charts(
    payload: ReportRequest,
    temp_dir: Path,
) -> tuple[dict[tuple[int, int], Path], list[str]]:
    font_registry = getattr(app.state, "font_registry", None)
    if font_registry is None:
        raise FontSetupError("中文字体尚未完成注册")

    chart_paths: dict[tuple[int, int], Path] = {}
    warnings: list[str] = []
    color_map: dict[str, str] = {}
    for section in payload.sections:
        for chart in section.charts:
            series = chart.labels if chart.type in {"pie", "doughnut"} else [dataset.label for dataset in chart.datasets]
            for label in series:
                color_map.setdefault(label, DEFAULT_PALETTE[len(color_map) % len(DEFAULT_PALETTE)])
    chart_sequence = 0
    for section_index, section in enumerate(payload.sections):
        for chart_index, chart in enumerate(section.charts):
            try:
                result = render_chart(
                    chart,
                    temp_dir / f"chart_{chart_sequence:03d}.png",
                    font_registry,
                    color_map,
                )
                chart_paths[(section_index, chart_index)] = result.path
                warnings.extend(result.warnings)
            except ChartDataError as exc:
                warnings.append(f"图表《{chart.title}》未加入PDF：{exc}")
            except Exception as exc:
                warnings.append(f"图表《{chart.title}》生成失败：{exc}")
            finally:
                chart_sequence += 1

    return chart_paths, warnings


def _build_report_pdf(
    payload: ReportRequest,
    output_path: Path,
    image_paths: dict[tuple[int, int], ResolvedImage],
    chart_paths: dict[tuple[int, int], Path],
) -> BuildResult:
    font_registry = getattr(app.state, "font_registry", None)
    if font_registry is None:
        raise FontSetupError("中文字体尚未完成注册")

    theme = ReportTheme(
        regular_font=font_registry.regular_name,
        bold_font=font_registry.bold_name,
    )
    result = StrategicReportBuilder(font_registry, theme).build(
        payload,
        image_paths,
        chart_paths,
        output_path,
    )
    return result


async def _resolve_images_concurrently(
    payload: ReportRequest,
    client: httpx.AsyncClient,
    temp_dir: Path,
) -> tuple[dict[tuple[int, int], ResolvedImage], list[str]]:
    """Resolve report images with deterministic ordering and at most four jobs."""
    async def resolve_one(
        section_index: int,
        image_index: int,
        image_spec: ImageSpec,
    ) -> tuple[tuple[int, int], ResolvedImage | None, str | None]:
        image_dir = temp_dir / f"image_{section_index:03d}_{image_index:03d}"
        image_dir.mkdir(parents=True, exist_ok=True)
        async with _image_resolution_slot():
            try:
                resolved = await resolve_image(
                    image_spec,
                    ASSET_REGISTRY,
                    client,
                    image_dir,
                )
            except Exception as exc:
                return (
                    (section_index, image_index),
                    None,
                    f"图片《{image_spec.caption}》未加入PDF：{exc}",
                )
        if resolved.warning or resolved.path is None:
            detail = resolved.warning or "图片不可用"
            return (
                (section_index, image_index),
                None,
                f"图片《{image_spec.caption}》未加入PDF：{detail}",
            )
        return (section_index, image_index), resolved, None

    jobs = [
        resolve_one(section_index, image_index, image_spec)
        for section_index, section in enumerate(payload.sections)
        for image_index, image_spec in enumerate(section.images)
    ]
    if not jobs:
        return {}, []

    image_paths: dict[tuple[int, int], ResolvedImage] = {}
    warnings: list[str] = []
    for key, resolved, warning in await asyncio.gather(*jobs):
        if resolved is not None:
            image_paths[key] = resolved
        if warning is not None:
            warnings.append(warning)
    return image_paths, warnings


def _validate_payload(payload: ReportRequest) -> None:
    if len(payload.sections) > MAX_REPORT_SECTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "field": "sections",
                "message": f"Too many sections; maximum is {MAX_REPORT_SECTIONS}",
            },
        )
    image_count = sum(len(section.images) for section in payload.sections)
    if image_count > MAX_REPORT_IMAGES:
        raise HTTPException(
            status_code=422,
            detail={
                "field": "sections.images",
                "message": f"Too many images; maximum is {MAX_REPORT_IMAGES}",
            },
        )
    for section_index, section in enumerate(payload.sections):
        for chart_index, chart in enumerate(section.charts):
            try:
                validate_chart(chart)
            except ChartDataError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "field": f"sections.{section_index}.charts.{chart_index}",
                        "message": f"Chart '{chart.title}' is invalid: {exc}",
                    },
                ) from exc


@app.get("/health")
def health() -> dict | JSONResponse:
    if ASSET_REGISTRY_ERROR:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": APP_NAME,
                "version": "1.0.0",
                "detail": f"Asset registry failed: {ASSET_REGISTRY_ERROR}",
            },
        )
    font_error = getattr(app.state, "font_error", "font setup has not completed")
    if font_error:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": APP_NAME,
                "version": "1.0.0",
                "detail": f"Chinese font setup failed: {font_error}",
            },
        )
    return {"status": "ok", "service": APP_NAME, "version": "1.0.0"}


@app.post(
    "/v1/reports",
    dependencies=[Depends(require_auth)],
    response_model=ReportResponse,
)
async def create_report(payload: ReportRequest, request: Request) -> ReportResponse:
    if ASSET_REGISTRY_ERROR:
        raise HTTPException(status_code=503, detail=f"Asset registry failed: {ASSET_REGISTRY_ERROR}")
    font_error = getattr(app.state, "font_error", "font setup has not completed")
    if font_error:
        raise HTTPException(status_code=503, detail=f"Chinese font setup failed: {font_error}")
    _validate_payload(payload)
    report_id = uuid.uuid4().hex[:16]
    requested_name = payload.filename or payload.title
    filename = f"{report_id}_{_safe_filename(requested_name)}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename
    warnings: list[str] = []
    temp_dir = Path(tempfile.mkdtemp(prefix="dify-pdf-"))

    try:
        timeout = httpx.Timeout(connect=8, read=20, write=10, pool=10)
        headers = {"User-Agent": "Dify-PDF-Report-Tool/1.0"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            image_paths, image_warnings = await _resolve_images_concurrently(
                payload,
                client,
                temp_dir,
            )
        warnings.extend(image_warnings)

        chart_paths, chart_warnings = await _run_blocking(
            _render_report_charts,
            payload,
            temp_dir,
        )
        warnings.extend(chart_warnings)
        build_result = await _run_blocking(
            _build_report_pdf,
            payload,
            output_path,
            image_paths,
            chart_paths,
        )
        warnings.extend(build_result.warnings)

        quality = await _run_blocking(
            inspect_pdf,
            output_path,
            build_result.image_count,
            _required_chinese_markers(
                payload,
                image_paths=image_paths,
                rendered_image_keys=build_result.rendered_image_keys,
                rendered_chart_keys=build_result.rendered_chart_keys,
            ),
        )
        errors = list(getattr(quality, "errors", ()))
        if errors:
            raise RuntimeError(f"PDF quality check failed: {'；'.join(errors)}")
        inspected_page_count = int(getattr(quality, "page_count", 0))
        if inspected_page_count != build_result.page_count:
            raise RuntimeError(
                "PDF quality check failed: "
                f"builder reported {build_result.page_count} pages but inspection found "
                f"{inspected_page_count}"
            )
        warnings.extend(quality.warnings)

        rendered_pages = await _run_blocking(
            render_pages,
            output_path,
            temp_dir / "rendered",
        )
        if len(rendered_pages) != build_result.page_count:
            raise PdfRenderError(
                f"Poppler rendered {len(rendered_pages)} pages; "
                f"expected {build_result.page_count}"
            )
    except asyncio.CancelledError:
        output_path.unlink(missing_ok=True)
        raise
    except HTTPException:
        output_path.unlink(missing_ok=True)
        raise
    except Exception:
        output_path.unlink(missing_ok=True)
        logger.exception("PDF report generation failed report_id=%s", report_id)
        raise HTTPException(
            status_code=500,
            detail=f"PDF report generation failed; report_id={report_id}",
        ) from None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    base_url = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    quality_check = "passed_with_warnings" if warnings else "passed"
    encoded_filename = quote(filename, safe="")
    return ReportResponse(
        report_id=report_id,
        filename=filename,
        download_url=f"{base_url}/files/{encoded_filename}",
        page_count=build_result.page_count,
        image_count=build_result.image_count,
        warnings=warnings,
        quality_check=quality_check,
    )
