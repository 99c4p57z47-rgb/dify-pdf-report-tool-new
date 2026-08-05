"""Structural quality inspection for generated Chinese PDF reports."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from pypdf import PdfReader
from pypdf.generic import ContentStream

from app.models import QualitySummary


_SPARSE_TEXT_THRESHOLD = 20


def _summary(
    *,
    page_count: int,
    image_count: int,
    warnings: list[str],
    errors: list[str],
) -> QualitySummary:
    quality_check = "passed_with_warnings" if warnings else "passed"
    return QualitySummary(
        quality_check=quality_check,
        page_count=page_count,
        image_count=image_count,
        warnings=warnings,
        errors=errors,
    )


def _resolved(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


def _count_image_draws(
    stream: Any,
    resources: Any,
    reader: PdfReader,
    active_forms: set[object],
) -> int:
    content = ContentStream(stream, reader)
    resolved_resources = _resolved(resources) if resources is not None else {}
    xobjects = _resolved(resolved_resources.get("/XObject", {}))
    count = 0
    for operands, operator in content.operations:
        if operator == b"INLINE IMAGE":
            count += 1
            continue
        if operator != b"Do" or not operands:
            continue
        reference = xobjects.get(operands[0])
        if reference is None:
            continue
        target = _resolved(reference)
        subtype = str(target.get("/Subtype", ""))
        if subtype == "/Image":
            count += 1
            continue
        if subtype != "/Form":
            continue
        indirect = getattr(target, "indirect_reference", None)
        form_key: object = (
            (indirect.idnum, indirect.generation)
            if indirect is not None
            else id(target)
        )
        if form_key in active_forms:
            continue
        form_resources = target.get("/Resources", resolved_resources)
        count += _count_image_draws(
            target,
            form_resources,
            reader,
            active_forms | {form_key},
        )
    return count


def _count_page_image_draws(page: Any, reader: PdfReader) -> int:
    return _count_image_draws(
        page.get_contents(),
        page.get("/Resources", {}),
        reader,
        set(),
    )


def inspect_pdf(
    pdf_path: Path,
    expected_images: int,
    required_chinese_markers: Sequence[str] = (),
) -> QualitySummary:
    """Inspect a generated PDF and return fatal errors separately from warnings.

    Chinese markers are required only when the caller supplies markers derived
    from Chinese request content. Optional image shortfalls and unusually sparse
    pages remain degradations rather than fatal errors.
    """
    if isinstance(expected_images, bool) or expected_images < 0:
        raise ValueError("expected_images 必须是非负整数")

    path = Path(pdf_path)
    warnings: list[str] = []
    errors: list[str] = []
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise OSError("文件不存在或为空")
        reader = PdfReader(str(path), strict=True)
        pages = list(reader.pages)
        metadata = reader.metadata
    except Exception as exc:
        errors.append(f"PDF 无法读取：{exc}")
        return _summary(
            page_count=0,
            image_count=0,
            warnings=warnings,
            errors=errors,
        )

    page_count = len(pages)
    image_count = 0
    extracted_text: list[str] = []
    if page_count == 0:
        errors.append("PDF 页数为零")
    if not metadata:
        warnings.append("PDF 缺少文档元数据")

    for page_number, page in enumerate(pages, start=1):
        try:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
        except Exception as exc:
            errors.append(f"PDF 第 {page_number} 页尺寸无法读取：{exc}")
            width = height = 0
        if width <= 0 or height <= 0:
            errors.append(f"PDF 第 {page_number} 页尺寸为零")

        try:
            text = page.extract_text() or ""
        except Exception as exc:
            errors.append(f"PDF 第 {page_number} 页文本无法读取：{exc}")
            text = ""
        extracted_text.append(text)

        try:
            page_image_count = _count_page_image_draws(page, reader)
        except Exception as exc:
            errors.append(f"PDF 第 {page_number} 页图片结构无法读取：{exc}")
            page_image_count = 0
        image_count += page_image_count

        visible_text = "".join(text.split())
        if len(visible_text) < _SPARSE_TEXT_THRESHOLD and page_image_count == 0:
            warnings.append(f"PDF 第 {page_number} 页内容稀疏")

    normalized_text = "".join("".join(extracted_text).split())
    marker_counts = Counter(
        normalized
        for marker in required_chinese_markers
        if (normalized := "".join(str(marker).split()))
    )
    for normalized_marker, required_count in marker_counts.items():
        actual_count = normalized_text.count(normalized_marker)
        if actual_count >= required_count:
            continue
        if required_count == 1:
            errors.append(f"PDF 缺少必需的中文标记：{normalized_marker}")
        else:
            errors.append(
                "PDF 中文标记出现次数不足："
                f"{normalized_marker} 至少 {required_count} 次，实际 {actual_count} 次"
            )
    if image_count < expected_images:
        warnings.append(
            f"PDF 中仅检测到 {image_count} 张图片，少于预期的 {expected_images} 张"
        )

    return _summary(
        page_count=page_count,
        image_count=image_count,
        warnings=warnings,
        errors=errors,
    )


__all__ = ["inspect_pdf"]
