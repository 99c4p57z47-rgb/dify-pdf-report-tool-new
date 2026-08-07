from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MultimodalDeploymentTests(unittest.TestCase):
    def test_deployed_manifest_contains_curated_multimodal_assets(self):
        records = json.loads(
            (ROOT / "assets" / "manifest.json").read_text(encoding="utf-8")
        )["assets"]
        asset_ids = {record["asset_id"] for record in records}
        self.assertGreaterEqual(len(records), 100)
        self.assertIn("ht_generated_chart_001", asset_ids)
        self.assertIn("ht_color_material_001", asset_ids)
        self.assertIn("ht_consumer_market_001", asset_ids)

    def test_pdf_prompt_enforces_one_shot_visual_request(self):
        prompt = (ROOT / "DIFY_AGENT_SYSTEM_PROMPT_合并版.md").read_text(encoding="utf-8")
        self.assertIn("PDF一次调用强制规则（覆盖后文冲突条款）", prompt)
        self.assertIn("不得单独调用create_chart", prompt)
        self.assertIn("首次请求必须包含至少2个images和至少1个charts", prompt)


if __name__ == "__main__":
    unittest.main()
