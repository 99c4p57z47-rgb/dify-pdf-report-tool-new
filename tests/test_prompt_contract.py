from pathlib import Path


PROMPT = Path(__file__).resolve().parents[1] / "DIFY_AGENT_SYSTEM_PROMPT_完整最终版.md"


def test_server_assets_are_independent_from_knowledge_images():
    text = PROMPT.read_text(encoding="utf-8")
    assert "PDF服务器资产不依赖Dify知识库图片功能" in text
    assert "不得在create_industry_pdf返回结果前声称服务器图片不可用" in text


def test_pdf_preflight_maps_sources_before_first_call():
    text = PROMPT.read_text(encoding="utf-8")
    assert "先建立sources来源注册表" in text
    assert "每个section默认至少填写1个相关且真实存在的source_id" in text
    assert "每个executive_insight" in text and "每个chart" in text


def test_retry_reasoning_is_not_user_facing():
    text = PROMPT.read_text(encoding="utf-8")
    assert "不得向用户输出“已识别错误”“现立即修正”“重试调用”" in text
    assert "422修正过程必须静默执行" in text
