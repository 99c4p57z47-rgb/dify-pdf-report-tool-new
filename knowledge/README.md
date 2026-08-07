# 家纺行业报告轻量知识库

本目录供 Railway 上的 PDF 服务读取，不直接存放原始 PDF。

## 内容

- `reports/`：89 份清洗后的 Markdown 报告或公开来源卡片；
- `catalog.json`：报告与图片的统一机器目录；
- `source_cards.jsonl`：标准化来源字段；
- `asset_catalog.jsonl`：100 个精选服务器图片资产；
- 图片实体位于仓库根目录 `assets/`，避免重复占用空间。

## 使用边界

1. `content_status=source_card_only` 的资料不是完整报告，只能用于发现资料和初步背景。
2. 精确数值必须引用对应 `source_id`，不得把目录摘要包装成完整报告结论。
3. `is_securities=true` 的资料不得用于用户要求排除证券研报的任务。
4. PDF服务应从 `asset_catalog.jsonl` 选择相关图片，并从根目录 `assets/` 读取文件。

## 更新

运行：

```bash
python3 scripts/build_knowledge_bundle.py
```

脚本会重新生成整个目录，并验证报告ID、图片ID和文件路径。
