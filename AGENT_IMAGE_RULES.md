# Agent Rules for Internal Image Assets

1. Work only with assets that are approved for this private repository. Do not upload public-web images, personal data, credentials, or unverified copyrighted material.
2. Preserve provenance in every manifest record: report title, publisher, year, source page, and an accurate caption are mandatory. Do not invent those values.
3. Store image and optional data files under `assets/`. Manifest paths must be relative, must not contain `..`, and must not point outside that directory.
4. Every record must include exactly these compatible core fields: `asset_id`, `path`, `report_title`, `publisher`, `year`, `source_page`, `caption`, and `usage_scope`. Set `usage_scope` to `internal-analysis`.
5. Use a stable, descriptive, unique `asset_id`. Do not add duplicate files under different IDs: the validator rejects identical SHA-256 content.
6. Classify charts and tables with `asset_type: "data_chart"` or `asset_type: "data_table"`. Add `category` and `data_path` when useful; `data_path` should identify the backing data relative to `assets/`.
7. Before commit, run `python scripts/validate_assets.py`; resolve every error. Do not lower thresholds for the final full package without explicit approval.
8. After validation, run `python scripts/build_catalog.py` and commit its Markdown and JSONL outputs together with the manifest and asset files.
