from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


class HtmlRendererUnavailable(RuntimeError):
    """Raised when Chromium is not ready to render reports."""


@dataclass(frozen=True)
class HtmlRenderResult:
    page_count: int
    warnings: tuple[str, ...] = ()


PlaywrightFactory = Callable[[], Awaitable[Any]]
PageCounter = Callable[[Path], int]


async def _default_playwright_factory() -> Any:
    from playwright.async_api import async_playwright

    return await async_playwright().start()


def _default_page_counter(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


class HtmlPdfRenderer:
    """Reuse one Chromium browser while isolating every PDF in its own context."""

    def __init__(
        self,
        *,
        playwright_factory: PlaywrightFactory | None = None,
        page_counter: PageCounter | None = None,
        concurrency: int = 2,
        timeout_seconds: float = 120,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._playwright_factory = playwright_factory or _default_playwright_factory
        self._page_counter = page_counter or _default_page_counter
        self._semaphore = asyncio.Semaphore(concurrency)
        self._timeout_seconds = timeout_seconds
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self.error: str = ""

    @property
    def ready(self) -> bool:
        return self._browser is not None and not self.error

    async def start(self) -> None:
        if self.ready:
            return
        try:
            self._playwright = await self._playwright_factory()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self.error = ""
        except Exception as exc:
            self.error = str(exc)
            await self.stop()
            self.error = str(exc)
            raise HtmlRendererUnavailable(
                f"Chromium renderer failed to start: {exc}"
            ) from exc

    async def stop(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = None
        self._playwright = None
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    async def render(self, html: str, output_path: Path) -> HtmlRenderResult:
        if not self.ready:
            raise HtmlRendererUnavailable(
                self.error or "Chromium renderer has not been started"
            )

        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = output_path.with_suffix(".render.html")
        html_path.write_text(html, encoding="utf-8")
        try:
            async with self._semaphore:
                async with asyncio.timeout(self._timeout_seconds):
                    context = await self._browser.new_context()
                    page = None
                    try:
                        page = await context.new_page()
                        await page.goto(html_path.as_uri(), wait_until="load")
                        await page.evaluate("document.fonts.ready")
                        await page.wait_for_function(
                            """() => Array.from(document.images).every(
                                (image) => image.complete && image.naturalWidth > 0
                            )""",
                            timeout=30_000,
                        )
                        await page.emulate_media(media="print")
                        await page.pdf(
                            path=str(output_path),
                            format="A4",
                            print_background=True,
                            prefer_css_page_size=True,
                            tagged=True,
                        )
                    finally:
                        if page is not None:
                            await page.close()
                        await context.close()
        finally:
            html_path.unlink(missing_ok=True)

        page_count = await asyncio.to_thread(self._page_counter, output_path)
        return HtmlRenderResult(page_count=page_count)
