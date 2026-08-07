from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def openapi() -> dict:
    schema_path = Path(__file__).resolve().parents[1] / "dify_openapi.yaml"
    return yaml.safe_load(schema_path.read_text(encoding="utf-8"))


def test_openapi_exposes_asset_id_and_response_quality(openapi):
    image = openapi["components"]["schemas"]["Image"]
    assert "asset_id" in image["properties"]
    assert {"asset_id", "url"} in [set(item["required"]) for item in image["oneOf"]]
    response = openapi["components"]["schemas"]["ReportResponse"]
    assert "quality_check" in response["required"]


def test_openapi_image_locator_branches_preserve_model_xor(openapi):
    image = openapi["components"]["schemas"]["Image"]
    branches = {tuple(branch["required"]): branch for branch in image["oneOf"]}

    assert set(branches) == {("asset_id",), ("url",), ("asset_id", "url")}
    assert all("not" not in branch and "if" not in branch for branch in image["oneOf"])
    assert branches[("asset_id",)]["properties"]["asset_id"]["type"] == "string"
    assert branches[("asset_id",)]["properties"]["url"]["type"] == "null"
    assert branches[("url",)]["properties"]["asset_id"]["type"] == "null"
    assert branches[("url",)]["properties"]["url"]["type"] == "string"
    assert branches[("asset_id", "url")]["additionalProperties"] is False


def test_openapi_describes_both_422_response_shapes_and_503(openapi):
    responses = openapi["paths"]["/v1/reports"]["post"]["responses"]
    validation_schema = responses["422"]["content"]["application/json"]["schema"]
    variants = validation_schema["oneOf"]

    assert len(variants) == 2
    assert {"field", "message", "type"} <= set(variants[0]["properties"]["detail"]["items"]["required"])
    assert {"field", "message"} <= set(variants[1]["properties"]["detail"]["required"])
    assert "503" in responses
    assert "example" in responses["503"]["content"]["application/json"]


def test_openapi_matches_nullable_and_nonblank_model_fields(openapi):
    schemas = openapi["components"]["schemas"]
    image = schemas["Image"]["properties"]
    source = schemas["Source"]["properties"]

    assert "null" in image["asset_id"]["type"]
    assert "null" in image["source_url"]["type"]
    assert "null" in source["url"]["type"]
    assert source["organization"]["pattern"] == r".*\S.*"
    assert source["published_at"]["pattern"] == r".*\S.*"


def test_agent_prompt_allows_only_one_full_or_two_half_images_per_section():
    prompt_path = Path(__file__).resolve().parents[1] / "DIFY_AGENT_SYSTEM_PROMPT_带PDF生成.md"
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "每个关键章节最多一张 full 主图，或最多两张 half 小图" in prompt
    assert "每个关键章节1至2张" not in prompt
    assert "Choose at most one main image or two small images per section" in prompt
