"""Render PDF pages through Poppler for post-build visual inspection."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


_FONT_ERRORS = ("Unknown font tag", "No font in show")
_PAGE_NUMBER = re.compile(r"-(\d+)\.png$")


class PdfRenderError(RuntimeError):
    """Raised when Poppler cannot render every PDF page safely."""


def _stderr_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _page_number(path: Path) -> int:
    match = _PAGE_NUMBER.search(path.name)
    return int(match.group(1)) if match else 0


def render_pages(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = 120,
) -> list[Path]:
    """Render *pdf_path* to numbered PNG files using a bounded subprocess."""
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError("dpi 必须是正整数")

    source = Path(pdf_path)
    if not source.is_file():
        raise PdfRenderError(f"PDF 文件不存在：{source}")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for stale_page in destination.glob("page-*.png"):
        if stale_page.is_file() or stale_page.is_symlink():
            stale_page.unlink()
    prefix = destination / "page"
    arguments = [
        "pdftoppm",
        "-png",
        "-r",
        str(dpi),
        str(source),
        str(prefix),
    ]

    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PdfRenderError("Poppler pdftoppm 不可用") from exc
    except subprocess.TimeoutExpired as exc:
        stderr = _stderr_text(exc.stderr).strip()
        detail = f"：{stderr}" if stderr else ""
        raise PdfRenderError(f"Poppler pdftoppm 渲染超过 60 秒{detail}") from exc

    stderr = _stderr_text(completed.stderr).strip()
    for diagnostic in _FONT_ERRORS:
        if diagnostic in stderr:
            raise PdfRenderError(f"Poppler 字体渲染错误：{diagnostic}")
    if completed.returncode != 0:
        detail = stderr or f"退出码 {completed.returncode}"
        raise PdfRenderError(f"Poppler pdftoppm 渲染失败：{detail}")

    pages = sorted(
        (
            path
            for path in destination.glob("page-*.png")
            if path.is_file() and _PAGE_NUMBER.search(path.name)
        ),
        key=_page_number,
    )
    if not pages:
        raise PdfRenderError("Poppler pdftoppm 未生成任何页面图像")
    page_numbers = [_page_number(path) for path in pages]
    if page_numbers != list(range(1, len(pages) + 1)):
        raise PdfRenderError(
            f"Poppler 页面图像编号不连续：{', '.join(map(str, page_numbers))}"
        )
    return pages


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Input PDF")
    parser.add_argument("output_dir", type=Path, help="Output page-image directory")
    parser.add_argument("--dpi", type=int, default=120, help="Render resolution (default: 120)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    pages = render_pages(args.pdf, args.output_dir, dpi=args.dpi)
    print(
        json.dumps(
            {"pdf": str(args.pdf.resolve()), "output_dir": str(args.output_dir.resolve()), "pages": len(pages)},
            ensure_ascii=False,
        )
    )


__all__ = ["PdfRenderError", "render_pages"]


if __name__ == "__main__":
    main()
