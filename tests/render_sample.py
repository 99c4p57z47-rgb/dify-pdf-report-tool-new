"""Build an acceptance PDF with the real package, fonts, assets, and charts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True, help="ReportRequest JSON file")
    parser.add_argument("--output", type=Path, required=True, help="Generated PDF path")
    parser.add_argument(
        "--render-dir",
        type=Path,
        help="Optional directory for Poppler-rendered page PNGs",
    )
    return parser.parse_args()


def _configured_font_dir() -> tuple[Path | None, str]:
    """Return a deterministic Noto directory when one is explicitly available."""
    candidates = [
        os.getenv("CJK_FONT_DIR", "").strip(),
        os.getenv("CJK_TEST_FONT_DIR", "").strip(),
        str(ROOT / "fonts"),
        "/app/fonts",
    ]
    for value in candidates:
        if not value:
            continue
        candidate = Path(value).expanduser()
        if (
            (candidate / "NotoSansCJK-Regular.ttc").is_file()
            and (candidate / "NotoSansCJK-Bold.ttc").is_file()
        ):
            return candidate, "packaged-noto-cjk"
    return None, ""


def _prepare_font_dir(work_dir: Path) -> tuple[Path, str]:
    """Prepare the real registry input, with a documented macOS CI fallback."""
    configured, source = _configured_font_dir()
    if configured is not None:
        return configured, source

    system_regular = Path("/System/Library/Fonts/STHeiti Light.ttc")
    system_bold = Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if system_regular.is_file() and system_bold.is_file():
        aliases = work_dir / "font-aliases"
        aliases.mkdir(parents=True, exist_ok=True)
        (aliases / "NotoSansCJK-Regular.ttc").symlink_to(system_regular)
        (aliases / "NotoSansCJK-Bold.ttc").symlink_to(system_bold)
        return aliases, "macos-stheiti-local-acceptance-fallback"

    raise RuntimeError(
        "Chinese fonts unavailable: set CJK_FONT_DIR or CJK_TEST_FONT_DIR to a "
        "directory containing NotoSansCJK-Regular.ttc and NotoSansCJK-Bold.ttc"
    )


def _prepare_chart_dependencies() -> str:
    """Use installed Matplotlib, or a complete local uv wheel cache when offline."""
    if importlib.util.find_spec("matplotlib") is not None:
        return "installed-runtime"

    package_names = (
        "matplotlib",
        "contourpy",
        "cycler",
        "fonttools",
        "kiwisolver",
        "packaging",
        "pyparsing",
        "python-dateutil",
        "six",
    )
    wheel_cache = Path.home() / ".cache" / "uv" / "wheels-v5"
    archive_roots: list[Path] = []
    for package_name in package_names:
        candidates = sorted(
            path
            for path in wheel_cache.glob(f"**/{package_name}/*")
            if path.is_symlink() and not path.name.endswith(".http")
        )
        if not candidates:
            raise RuntimeError(
                "Matplotlib is unavailable in the active Python and the local uv cache "
                f"does not contain {package_name}; install requirements.txt first"
            )
        archive_roots.append(candidates[-1].resolve())
    sys.path[:0] = [str(path) for path in archive_roots]
    if importlib.util.find_spec("matplotlib") is None:
        raise RuntimeError("local uv cache did not expose Matplotlib")
    return "local-uv-wheel-cache"


def _resolve_images(payload, registry):
    images = {}
    warnings: list[str] = []
    for section_index, section in enumerate(payload.sections):
        for image_index, image_spec in enumerate(section.images):
            if image_spec.asset_id is None:
                raise RuntimeError(
                    "offline acceptance fixtures must use packaged asset_id images; "
                    f"sections.{section_index}.images.{image_index} uses a remote URL"
                )
            try:
                images[(section_index, image_index)] = registry.resolve(image_spec.asset_id)
            except Exception as exc:
                warnings.append(
                    f"图片《{image_spec.caption}》未加入PDF：资产 {image_spec.asset_id} 缺失或损坏：{exc}"
                )
    return images, warnings


def _render_charts(payload, work_dir: Path, fonts):
    from app.charts import DEFAULT_PALETTE, render_chart

    chart_paths = {}
    warnings: list[str] = []
    color_map: dict[str, str] = {}
    sequence = 0
    for section in payload.sections:
        for chart in section.charts:
            series = chart.labels if chart.type in {"pie", "doughnut"} else [item.label for item in chart.datasets]
            for label in series:
                color_map.setdefault(label, DEFAULT_PALETTE[len(color_map) % len(DEFAULT_PALETTE)])
    for section_index, section in enumerate(payload.sections):
        for chart_index, chart in enumerate(section.charts):
            result = render_chart(
                chart,
                work_dir / f"chart_{sequence:03d}.png",
                fonts,
                color_map,
            )
            chart_paths[(section_index, chart_index)] = result.path
            warnings.extend(result.warnings)
            sequence += 1
    return chart_paths, warnings


def _required_markers(payload) -> tuple[str, ...]:
    """Choose field-level CJK anchors for the production structural inspector."""
    markers = [payload.title[:10], payload.executive_summary[:10]]
    markers.extend(section.heading[:10] for section in payload.sections)
    markers.extend(source.title[:10] for source in payload.sources)
    return tuple(marker for marker in markers if marker)


def build_report(request_path: Path, output_path: Path, render_dir: Path | None) -> dict[str, object]:
    """Validate and render one request through the production package interfaces."""
    from app.assets import AssetRegistry
    from app.fonts import register_fonts, verify_font_probe
    from app.layout import ReportTheme, StrategicReportBuilder
    from app.models import ReportRequest
    from app.quality import inspect_pdf
    from scripts.render_pdf_pages import render_pages

    request_data = json.loads(request_path.read_text(encoding="utf-8"))
    payload = ReportRequest.model_validate(request_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dependency_source = _prepare_chart_dependencies()

    with tempfile.TemporaryDirectory(prefix="dify-pdf-acceptance-") as temporary:
        work_dir = Path(temporary)
        matplotlib_config = Path(tempfile.gettempdir()) / "dify-pdf-matplotlib-cache"
        matplotlib_config.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(matplotlib_config)
        font_dir, font_source = _prepare_font_dir(work_dir)
        font_registry = register_fonts(font_dir)
        verify_font_probe(font_registry, work_dir)

        registry = AssetRegistry.from_manifest(ROOT / "assets", ROOT / "assets" / "manifest.json")
        image_paths, image_warnings = _resolve_images(payload, registry)
        chart_paths, chart_warnings = _render_charts(payload, work_dir, font_registry)
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

        warnings = [*image_warnings, *chart_warnings, *result.warnings]
        quality = inspect_pdf(
            output_path,
            result.image_count,
            _required_markers(payload),
        )
        if quality.errors:
            raise RuntimeError("PDF quality check failed: " + "；".join(quality.errors))
        if quality.page_count != result.page_count:
            raise RuntimeError(
                f"page count mismatch: builder={result.page_count}, quality={quality.page_count}"
            )
        warnings.extend(quality.warnings)

        rendered_pages: list[Path] = []
        if render_dir is not None:
            rendered_pages = render_pages(output_path, render_dir)
            if len(rendered_pages) != result.page_count:
                raise RuntimeError(
                    f"render count mismatch: expected={result.page_count}, actual={len(rendered_pages)}"
                )

    return {
        "pdf": str(output_path.resolve()),
        "request": str(request_path.resolve()),
        "page_count": result.page_count,
        "image_count": result.image_count,
        "quality_check": "passed_with_warnings" if warnings else "passed",
        "warnings": warnings,
        "font_source": font_source,
        "chart_dependency_source": dependency_source,
        "rendered_pages": len(rendered_pages),
    }


def main() -> None:
    args = _parse_args()
    request_path = args.request.resolve()
    output_path = args.output.resolve()
    render_dir = args.render_dir.resolve() if args.render_dir else None
    result = build_report(request_path, output_path, render_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
