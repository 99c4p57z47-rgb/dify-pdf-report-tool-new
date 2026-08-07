"""Small in-process index for fast, source-backed PDF assembly."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import FastReportRequest, ImageSpec, ReportRequest, SectionSpec, SourceSpec


_LATIN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]+")


def _tokens(value: str) -> set[str]:
    text = value.lower()
    result = set(_LATIN.findall(text))
    for run in _CJK.findall(text):
        result.update(run)
        result.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return {token for token in result if token.strip()}


def _score(query: set[str], value: str) -> float:
    if not query:
        return 0.0
    candidate = _tokens(value)
    return sum(2.0 if len(token) > 1 else 0.35 for token in query & candidate)


@dataclass(frozen=True)
class KnowledgeStore:
    reports: tuple[dict[str, Any], ...]
    assets: tuple[dict[str, Any], ...]
    asset_dir: Path

    @classmethod
    def from_catalog(cls, catalog_path: Path, asset_dir: Path) -> "KnowledgeStore":
        catalog_path = Path(catalog_path)
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        reports = []
        for record in data.get("reports", []):
            normalized = dict(record)
            report_path = catalog_path.parent / str(record.get("report_path", ""))
            if report_path.is_file():
                normalized["search_text"] = report_path.read_text(encoding="utf-8")[:12000]
            else:
                normalized["search_text"] = ""
            reports.append(normalized)
        return cls(tuple(reports), tuple(data.get("assets", [])), Path(asset_dir))

    def search(self, query: str, *, limit: int = 4) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for report in self.reports:
            if report.get("is_securities"):
                continue
            title_score = _score(query_tokens, str(report.get("title", ""))) * 3
            category_score = _score(query_tokens, str(report.get("category", "")))
            body_score = min(8.0, _score(query_tokens, str(report.get("search_text", ""))) * 0.15)
            freshness = 0.2 if "2026" in str(report.get("published_at", "")) else 0.0
            ranked.append((title_score + category_score + body_score + freshness, report))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("source_id", ""))))
        positive = [report for score, report in ranked if score > 0]
        return (positive or [report for _, report in ranked])[:limit]

    def _images(
        self,
        query: str,
        *,
        limit: int,
        exclude_asset_ids: set[str] | None = None,
    ) -> list[ImageSpec]:
        if limit <= 0:
            return []
        query_tokens = _tokens(query)
        ranked: list[tuple[float, dict[str, Any]]] = []
        excluded = exclude_asset_ids or set()
        for asset in self.assets:
            if str(asset.get("asset_id", "")) in excluded:
                continue
            path = self.asset_dir.parent / str(asset.get("repo_path", ""))
            if not path.is_file():
                continue
            text = " ".join(
                str(asset.get(key, ""))
                for key in ("caption", "report_title", "category", "asset_type")
            )
            score = _score(query_tokens, text)
            if asset.get("asset_type") == "data_chart" and re.search(r"市场|规模|增长|消费|数据|销售|渠道", query):
                score += 3
            ranked.append((score, asset))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("asset_id", ""))))
        images = []
        for score, asset in ranked:
            if score <= 0:
                continue
            images.append(
                ImageSpec(
                    asset_id=str(asset["asset_id"]),
                    caption=str(asset.get("caption") or asset.get("report_title") or "知识库图片"),
                    source=str(asset.get("publisher") or "服务器知识库"),
                    page=str(asset.get("source_page") or ""),
                    report_title=str(asset.get("report_title") or ""),
                    publisher=str(asset.get("publisher") or "") or None,
                    year=asset.get("year") if isinstance(asset.get("year"), int) else None,
                    source_page=asset.get("source_page") if isinstance(asset.get("source_page"), int) else None,
                    layout="full",
                )
            )
            if len(images) >= limit:
                break
        return images

    def enrich(self, request: FastReportRequest) -> ReportRequest:
        report_by_id = {str(report.get("source_id")): report for report in self.reports}
        used_sources: dict[str, dict[str, Any]] = {}
        used_asset_ids: set[str] = set()
        sections: list[SectionSpec] = []
        for section in request.sections:
            query = " ".join([request.title, section.heading, *section.key_points])
            matches = [
                report_by_id[item]
                for item in section.source_ids
                if item in report_by_id and not report_by_id[item].get("is_securities")
            ]
            if not matches:
                matches = self.search(query, limit=4)
            source_ids = []
            for report in matches:
                source_id = str(report.get("source_id", ""))
                if source_id and source_id not in source_ids:
                    source_ids.append(source_id)
                    used_sources[source_id] = report
            images = (
                self._images(
                    query,
                    limit=request.max_images_per_section,
                    exclude_asset_ids=used_asset_ids,
                )
                if request.include_images
                else []
            )
            used_asset_ids.update(image.asset_id for image in images if image.asset_id)
            sections.append(
                SectionSpec(
                    heading=section.heading,
                    summary=section.summary,
                    body_markdown=section.body_markdown,
                    key_points=section.key_points,
                    source_ids=source_ids,
                    images=images,
                )
            )

        sources = []
        for source_id, record in used_sources.items():
            url = str(record.get("source_url") or "")
            sources.append(
                SourceSpec(
                    source_id=source_id,
                    title=str(record.get("title") or source_id),
                    organization=str(record.get("organization") or "来源机构待核验"),
                    published_at=str(record.get("published_at") or "年份未标注"),
                    url=url if url.startswith("https://") else None,
                    source_type=str(record.get("content_status") or "knowledge_base"),
                )
            )
        methodology = request.methodology or "服务器在本地知识目录中按标题、章节和关键词检索，并自动匹配来源与精选图片资产。"
        return ReportRequest(
            title=request.title,
            subtitle=request.subtitle,
            author=request.author,
            generated_at=request.generated_at,
            executive_summary=request.executive_summary,
            sections=sections,
            sources=sources,
            methodology=methodology,
            disclaimer=request.disclaimer,
            filename=request.filename,
        )
