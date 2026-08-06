from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACK_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = PACK_ROOT / "scripts" / "validate_assets.py"
CATALOG_BUILDER = PACK_ROOT / "scripts" / "build_catalog.py"


class AssetScriptTests(unittest.TestCase):
    def test_validator_reports_missing_fields_and_duplicate_ids(self) -> None:
        """Catches a validator that accepts an unusable manifest record."""
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "assets": [
                            {"asset_id": "same-id", "path": "missing-one.png"},
                            {"asset_id": "same-id", "path": "missing-two.png"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(VALIDATOR), "--manifest", str(manifest_path)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing required fields", result.stderr)
        self.assertIn("duplicate asset_id: same-id", result.stderr)

    def test_catalog_builder_writes_markdown_and_jsonl_cards(self) -> None:
        """Catches a catalog builder that loses the manifest metadata on export."""
        record = {
            "asset_id": "chart-001",
            "path": "charts/chart-001.png",
            "report_title": "Home Textile Outlook",
            "publisher": "Example Research",
            "year": 2026,
            "source_page": 7,
            "caption": "Category growth by channel.",
            "usage_scope": "internal-analysis",
            "category": "channel",
            "asset_type": "data_chart",
            "data_path": "data/chart-001.csv",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "knowledge_cards"
            manifest_path.write_text(json.dumps({"assets": [record]}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(CATALOG_BUILDER),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            markdown = (output_dir / "asset_catalog.md").read_text(encoding="utf-8")
            jsonl = (output_dir / "asset_catalog.jsonl").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("chart-001", markdown)
        self.assertIn("Category growth by channel.", markdown)
        self.assertEqual(json.loads(jsonl), record)


if __name__ == "__main__":
    unittest.main()
