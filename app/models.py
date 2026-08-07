from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


_PLACEHOLDER_TOKENS = ("XXXX", "TBD")
_FICTITIOUS_STANDARD_PATTERN = re.compile(
    r"\b(?:GB(?:/T)?|ISO|IEC|EN|ASTM)\s*(?:X{2,}|TBD)(?:-\d{4})?\b",
    re.IGNORECASE,
)
_ASSET_ID_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*")
_PRECISE_NUMERIC_PATTERN = re.compile(
    r"(?:"
    r"\d+(?:\.\d+)?\s*[%％]"
    r"|(?:¥|￥|\$|USD|CNY|RMB)\s*\d+(?:\.\d+)?"
    r"|\d+\.\d+"
    r"|\d+(?:\.\d+)?\s*(?:万|亿|千|百|个|件|套|元|美元|人民币|亿元|万元|吨|公斤|千克|米|平方米|%|％)"
    r")",
    re.IGNORECASE,
)


def _iter_text(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text(item)


def _contains_precise_numeric(value: Any) -> bool:
    return any(_PRECISE_NUMERIC_PATTERN.search(text) for text in _iter_text(value))


class SourceSpec(BaseModel):
    source_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    organization: str = Field(min_length=1, max_length=200)
    published_at: str = Field(min_length=1, max_length=80)
    data_period: str = Field(default="", max_length=120)
    url: HttpUrl | None = Field(default=None, max_length=2000)
    source_type: str = Field(default="", max_length=80)
    accessed_at: str = Field(default="", max_length=80)

    @field_validator("organization", "published_at")
    @classmethod
    def validate_required_metadata(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("来源机构和发布日期不能为空白")
        return normalized

    @field_validator("url")
    @classmethod
    def validate_source_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("来源 URL 必须使用 HTTPS")
        return value


class ImageSpec(BaseModel):
    asset_id: str | None = Field(default=None, min_length=1, max_length=300)
    url: HttpUrl | None = Field(default=None, max_length=3000)
    caption: str = Field(min_length=1, max_length=600)
    source: str = Field(default="", max_length=400)
    page: str = Field(default="", max_length=40)
    alt: str = Field(default="", max_length=300)
    layout: Literal["full", "half"] = "full"
    fit: Literal["contain", "cover"] = "contain"
    report_title: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    year: int | None = Field(default=None, ge=1, le=9999)
    source_page: int | None = Field(default=None, ge=1)
    source_name: str | None = Field(default=None, max_length=300)
    source_url: HttpUrl | None = Field(default=None, max_length=2000)

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str | None) -> str | None:
        if value is not None and not _ASSET_ID_PATTERN.fullmatch(value):
            raise ValueError("asset_id 必须是稳定 ID，不能包含路径、协议或路径穿越")
        return value

    @field_validator("url", "source_url")
    @classmethod
    def validate_https_urls(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("图片及来源 URL 必须使用 HTTPS")
        return value

    @model_validator(mode="after")
    def validate_locator(self) -> ImageSpec:
        if (self.asset_id is None) == (self.url is None):
            raise ValueError("asset_id 与 url 必须且只能提供一个")
        return self


class DatasetSpec(BaseModel):
    label: str = Field(min_length=1, max_length=150)
    data: list[float] = Field(min_length=1, max_length=30)


class ChartSpec(BaseModel):
    type: Literal["bar", "horizontal_bar", "line", "pie", "doughnut"]
    title: str = Field(min_length=1, max_length=220)
    labels: list[str] = Field(min_length=2, max_length=30)
    datasets: list[DatasetSpec] = Field(min_length=1, max_length=6)
    unit: str = Field(default="", max_length=60)
    source: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=500)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class ExecutiveInsightSpec(BaseModel):
    claim: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=4000)
    implication: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class SectionSpec(BaseModel):
    heading: str = Field(min_length=1, max_length=220)
    summary: str = Field(default="", max_length=1500)
    body_markdown: str = Field(default="", max_length=30000)
    key_points: list[str] = Field(default_factory=list, max_length=20)
    images: list[ImageSpec] = Field(default_factory=list, max_length=8)
    charts: list[ChartSpec] = Field(default_factory=list, max_length=6)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class FastSectionSpec(BaseModel):
    heading: str = Field(min_length=1, max_length=220)
    summary: str = Field(default="", max_length=1500)
    body_markdown: str = Field(default="", max_length=12000)
    key_points: list[str] = Field(default_factory=list, max_length=12)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class FastReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    subtitle: str = Field(default="", max_length=300)
    author: str = Field(default="家纺行业报告分析智能体", max_length=160)
    generated_at: str = Field(default="", max_length=80)
    year: int | None = Field(default=None, ge=2000, le=2100)
    executive_summary: str = Field(default="", max_length=4000)
    sections: list[FastSectionSpec] = Field(min_length=1, max_length=12)
    methodology: str = Field(default="", max_length=3000)
    disclaimer: str = Field(
        default="本报告基于服务器知识库和已注明资料生成，仅供行业研究与产品规划参考。",
        max_length=2000,
    )
    filename: str = Field(default="", max_length=160)
    include_images: bool = True
    max_images_per_section: int = Field(default=1, ge=0, le=2)


class ReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    subtitle: str = Field(default="", max_length=300)
    author: str = Field(default="家纺行业报告分析智能体", max_length=160)
    generated_at: str = Field(default="", max_length=80)
    executive_summary: str = Field(default="", max_length=6000)
    executive_insights: list[ExecutiveInsightSpec] = Field(min_length=1, max_length=5, default_factory=list)
    sections: list[SectionSpec] = Field(min_length=1, max_length=30)
    sources: list[SourceSpec] = Field(default_factory=list, max_length=100)
    methodology: str = Field(default="", max_length=5000)
    disclaimer: str = Field(
        default="本报告基于已注明的内部知识库资料和公开来源生成。预测、估算和综合判断不应被视为报告原文或投资建议。",
        max_length=3000,
    )
    filename: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_content_and_sources(self) -> ReportRequest:
        serialized = self.model_dump(mode="json")
        for text in _iter_text(serialized):
            if any(token in text.upper() for token in _PLACEHOLDER_TOKENS) or _FICTITIOUS_STANDARD_PATTERN.search(text):
                raise ValueError("报告包含占位内容")

        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id 必须唯一")
        available_source_ids = set(source_ids)

        references: list[str] = []
        for insight in self.executive_insights:
            references.extend(insight.source_ids)
        for section in self.sections:
            references.extend(section.source_ids)
            for chart in section.charts:
                references.extend(chart.source_ids)
        unknown_source_ids = sorted(set(references) - available_source_ids)
        if unknown_source_ids:
            raise ValueError(f"source_ids 包含不存在的来源 ID: {', '.join(unknown_source_ids)}")

        for section_index, section in enumerate(self.sections):
            for chart_index, chart in enumerate(section.charts):
                if not chart.source_ids:
                    raise ValueError(
                        f"sections.{section_index}.charts.{chart_index}.source_ids："
                        "包含结构化 datasets 数值的图表必须提供 source_ids"
                    )
        for index, section in enumerate(self.sections, start=1):
            if _contains_precise_numeric(section.model_dump(mode="json")) and not section.source_ids:
                raise ValueError(f"sections.{index - 1}.source_ids：包含精确数值的章节必须提供 source_ids")
        for index, insight in enumerate(self.executive_insights, start=1):
            if _contains_precise_numeric(insight.model_dump(mode="json")) and not insight.source_ids:
                raise ValueError(f"executive_insights.{index - 1}.source_ids：包含精确数值的观点必须提供 source_ids")
        return self


class QualitySummary(BaseModel):
    quality_check: Literal["passed", "passed_with_warnings"]
    warnings: list[str] = Field(default_factory=list)
    page_count: int = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)

    @property
    def status(self) -> Literal["passed", "passed_with_warnings", "failed"]:
        if self.errors:
            return "failed"
        return self.quality_check


class ReportResponse(BaseModel):
    success: bool = True
    report_id: str
    filename: str
    download_url: str
    page_count: int
    image_count: int
    warnings: list[str]
    quality_check: Literal["passed", "passed_with_warnings"]
