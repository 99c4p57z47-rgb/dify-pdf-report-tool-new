# Dify 家纺行业 PDF 报告生成工具

该服务由 Dify Agent 调用，负责将已核验的研究内容、知识库图片资产、数据图表和来源信息排版为中文 PDF。服务不会搜索事实或补充内容；Agent 必须对资料、图片和数值负责。

`create_industry_pdf` 只有在响应中的 `quality_check` 为 `passed`，或为 `passed_with_warnings` 且已向用户披露 `warnings` 时，才算完成。

## 请求与图片资产

`dify_openapi.yaml` 是 Dify 导入文件，字段与 `app.models.ReportRequest` 和 `ReportResponse` 保持一致。`sample_request.json` 是可审计的集成样例，包含：

- 知识库试点资产 `report_012_p001_figure_01`；
- 显式数据、单位、来源和 `source_ids` 的图表；
- 三条执行洞察；
- 用于检验自动分页的长 Markdown 表格；
- 完整的方法说明和未编号的来源标题。

### 资产整理

1. 将可供 PDF 使用的图片放在 `assets/`，并由 `scripts/build_asset_manifest.py` 生成或更新 `assets/manifest.json`。
2. 每个资产必须拥有稳定、不可猜测为路径的 `asset_id`，并保留 `report_title`、`publisher`、`year`、`source_page`、`caption` 和使用范围。
3. 将这些元数据连同图片 asset_id 写入 Dify 知识库，使 Agent 只能传回知识库实际返回的 asset_id。
4. 不要把 `../assets/...`、本机路径、`file://` 或相对 Markdown 路径传给 API；绝不将相对路径改写为猜测 URL。
5. 一节至多使用一张主图或两张小图。没有与结论直接相关的真实图片时，省略 `images`。

外部图片只可填写可访问的 HTTPS `url`；`asset_id` 和 `url` 必须二选一。每张图片还应尽可能保留原报告名、机构、年份和页码。

## 环境变量

复制示例后再填入真实值：

```bash
cp .env.example .env
openssl rand -hex 32
```

| 变量 | 必填 | 说明 |
|---|---:|---|
| `PDF_TOOL_API_TOKEN` | 是 | Dify 以 `Authorization: Bearer <token>` 发送的长随机密钥。 |
| `PUBLIC_BASE_URL` | 是 | 用户和 Dify 都能访问的公开 HTTPS 地址；若经 Worker 代理，应填 Worker URL。 |
| `CJK_FONT_DIR` | 是 | 容器内含 Noto Sans CJK Regular 与 Bold 字体的目录。 |
| `IMAGE_HOST_ALLOWLIST` | 视情况 | PDF 服务可访问图片的明确域名列表；不要使用 localhost、私网地址或通配符。 |
| `PDF_ASSET_DIR` | 否 | 资产目录，默认 `./assets`。 |
| `PDF_OUTPUT_DIR` | 否 | PDF 输出目录，默认 `./output`。 |
| `MAX_IMAGE_BYTES` | 否 | 单张远程图片的最大下载大小。 |
| `MAX_REPORT_SECTIONS` | 否 | 服务端章节上限，默认 20。 |
| `MAX_REPORT_IMAGES` | 否 | 服务端图片上限，默认 30。 |
| `ENABLE_ASSET_PREVIEW` | 否 | 仅受控环境中启用资产预览，默认关闭。 |

不要在 Agent 系统提示词或聊天内容中保存 Token。Dify 工具凭据中保存 Token，服务端 `.env` 中保存同一值。

## 本地测试与运行

需要 Python 3.11+ 和系统级 PDF/字体依赖。开发环境安装后执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/test_openapi.py -v
pytest tests -v
```

本地启动：

```bash
set -a
. .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

提交示例：

```bash
curl -X POST http://127.0.0.1:8000/v1/reports \
  -H "Authorization: Bearer $PDF_TOOL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @sample_request.json
```

## Docker 构建与部署

本地构建与检查：

```bash
docker build -t dify-pdf-report-tool .
docker run --rm -p 8000:8000 --env-file .env dify-pdf-report-tool
curl http://127.0.0.1:8000/health
```

或使用 Compose：

```bash
docker compose up -d --build
docker compose ps
```

生产环境必须通过 HTTPS 反向代理或平台域名提供服务，不能直接公开容器的 8000 端口。

### Railway

1. 将 `dify_pdf_report_tool` 作为独立 Git 仓库或 Railway 服务根目录部署，Railway 会使用 `Dockerfile` 构建。
2. 在 Railway Variables 中配置 `PDF_TOOL_API_TOKEN`、`PUBLIC_BASE_URL`、`CJK_FONT_DIR` 和必要的 `IMAGE_HOST_ALLOWLIST`。
3. 为服务生成 Railway HTTPS 域名或绑定自有域名，并将该完整公网地址设为 `PUBLIC_BASE_URL`。
4. 部署后访问 `/health`，再以 `sample_request.json` 和 Bearer Token 发送请求。
5. 若使用 Cloudflare Worker，`PUBLIC_BASE_URL` 填 Worker 的 HTTPS URL，而 Railway 后端 URL 只作为 Worker 的上游地址。

### Cloudflare Worker 代理

Worker 可为 Railway 或自托管 API 提供稳定域名、TLS 和访问策略。创建 Worker secret 或变量 `PDF_ORIGIN`，值为后端 HTTPS 根地址（例如 Railway 域名）；部署以下最小代理：

```javascript
export default {
  async fetch(request, env) {
    const upstream = new URL(request.url)
    const origin = new URL(env.PDF_ORIGIN)
    upstream.protocol = origin.protocol
    upstream.host = origin.host
    return fetch(new Request(upstream, request))
  },
}
```

在 Worker 中限制允许的方法、路径和来源，按需加入 WAF 或 Access；不要记录 `Authorization` 请求头。部署后记录完整地址，例如 `https://company-pdf-tool.workers.dev`。

## 导入 Dify 自定义工具

1. 打开 Dify：`工具 → 自定义 → 创建自定义工具`。
2. 在 `dify_openapi.yaml` 中将：

   ```yaml
   https://YOUR-PDF-TOOL-WORKER.workers.dev
   ```

   替换为实际部署的完整 Cloudflare Worker URL，例如：

   ```yaml
   https://company-pdf-tool.workers.dev
   ```

   必须在导入前完成替换；占位地址无法路由到服务。
3. 粘贴完整 YAML，并确认 Dify 识别的 `operationId` 为 `create_industry_pdf`。
4. 配置 Bearer Token 凭据：Header 名称为 `Authorization`，值为 `Bearer <PDF_TOOL_API_TOKEN>`。
5. 保存并用 `sample_request.json` 进行调用测试。
6. 将 `DIFY_AGENT_SYSTEM_PROMPT_带PDF生成.md` 作为 Agent 的完整系统提示词，确保 Agent 遵守资产、来源、图表和质量状态规则。

## 响应与告警

成功响应始终包含：

```json
{
  "success": true,
  "download_url": "https://company-pdf-tool.workers.dev/files/report.pdf",
  "page_count": 8,
  "image_count": 1,
  "warnings": [],
  "quality_check": "passed"
}
```

- `passed`：可向用户交付下载链接。
- `passed_with_warnings`：可交付，但 Agent 必须阅读并如实说明 `warnings`，例如被省略的图片或图表。
- 任何其他状态或非 200 响应：不能声称 PDF 已完成，也不能编造下载链接。

## 故障排查

| 状态 | 常见原因 | 处理方式 |
|---|---|---|
| 401 | Token 缺失、Bearer 前缀不正确，或 Dify 与服务端 Token 不一致。 | 在 Dify 凭据和服务器变量中使用同一随机 Token；重新部署后再测试。 |
| 422 | 请求未满足模型约束，例如图片同时传 asset_id 和 url、图表缺少 source_ids、来源元数据不完整、URL 非 HTTPS。 | 请求模型错误使用 `detail` 数组，读取每项的 `field`、`message`、`type`；服务端运行时限制使用 `detail` 对象，读取其 `field` 和 `message`。只修正指出的字段后重试一次。 |
| 500 | 字体、资产清单、远程图片、图表渲染、PDF 布局或质量检查失败。 | 查看服务日志和 `/health`；检查字体目录、资产 manifest、允许域名、Worker 上游和磁盘写入权限。 |
| 503 | 服务尚未就绪，例如资产清单无法加载或中文字体初始化失败。 | 先访问 `/health` 查看具体原因；修复 `PDF_ASSET_DIR`/manifest 或 `CJK_FONT_DIR` 后重新部署，再提交报告请求。 |

出现图片或图表降级时，即使响应为 200，也要检查 `warnings`。不要为了消除警告而猜测图片 URL、页码或来源；应修复资料来源后重新生成。

## Task 8 验收状态（2026-08-05）

本地真实包验收已通过：`sample_request.json` 生成 7 页、2 个图片／图表对象，压力夹具生成 28 页、6 个图片／图表对象；两者结构质量均为 `passed` 且无 warnings。Poppler 分别渲染 7 和 28 张页面 PNG，没有字体错误。压力报告的 28 页均含可提取中文，目录跨 2 页，100 行表格完整跨页，30 条来源编号唯一。七页代表性视觉门记录在 `tests/golden/README.md`。

Fix Round 1 已验证 `ImageSpec.layout`：连续两个 `half` 进入双列（每列图片绘制宽度 238.1102 pt），单独 `half` 使用左侧半宽列，`full` 保持 493.2283 pt；`contain` 比例、图注同行、`image_count` 与 `rendered_image_keys` 均通过真实 PDF 回归。压力夹具只跨章节保留一次 `half` 和一次 `full`，避免重复展示唯一试点资产。旧的 `tests/output/rendered*` 已完整移至 `tests/output/legacy/`，当前页面输出只看 `sample-pages/` 和 `stress-pages/`。

从项目根目录复现：

```bash
PYTHON=/Users/liyuxuan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
$PYTHON tests/render_sample.py --request sample_request.json --output tests/output/sample.pdf
$PYTHON tests/render_sample.py --request tests/fixtures/stress_request.json --output tests/output/stress.pdf
$PYTHON scripts/render_pdf_pages.py tests/output/sample.pdf tests/output/sample-pages
$PYTHON scripts/render_pdf_pages.py tests/output/stress.pdf tests/output/stress-pages
```

`tests/render_sample.py` 使用真实 `ReportRequest`、资产 manifest、字体注册和探针、Matplotlib 图表、Platypus 布局、pypdf 质量检查及 Poppler helper。正常开发／容器环境应先安装 `requirements.txt` 并提供 Dockerfile 中的 Noto CJK 字体。本机缺少 Matplotlib 安装时，验收 runner 可只读使用完整的本地 uv wheel cache；本机缺少 Noto 文件时使用 macOS STHeiti 注册探针回退，并在 JSON 结果中明确标记来源。该回退只证明本地中文排版链路，不替代容器内 Noto 验收。

Docker／HTTP 容器验收为 pending：本机 `command -v docker` 返回 1（`docker: command not found`），因此未构建或启动容器，也没有伪造 `/health`、POST 或下载结果。Docker 可用后执行本节前述构建命令，或按 Task 8 使用端口 18000、`test-token` 和 `http://127.0.0.1:18000` 完成 health、Bearer POST、响应页数／图片数／质量状态及文件下载检查。

Railway、Cloudflare Worker `https://dify-pdf-proxy.99c4p57z47.workers.dev` 和 Dify 重导入均为 pending：当前环境没有已认证的 Railway／Cloudflare／Dify 会话、后端 origin、API token 或明确 Dify workspace，因此没有进行外部变更或声称远端通过。获得明确上下文后依次：

1. 在 Railway 部署本目录 Dockerfile，配置 `PDF_TOOL_API_TOKEN`、Worker 公网 `PUBLIC_BASE_URL` 和容器 `/app/fonts` 的 `CJK_FONT_DIR`，验证 Railway `/health` 与 Bearer POST。
2. 将 Worker 的 `PDF_ORIGIN` 指向明确的 Railway HTTPS origin，部署后对上述 Worker URL 检查 `/health`、Bearer 转发、无效 JSON 的结构化 422 `detail`、成功响应的下载 URL 和文件下载，并确认无 SSL EOF。
3. 把 `dify_openapi.yaml` 的 server URL 设为上述 Worker，重导入 Dify 自定义工具并配置同一 Bearer token；用 `report_012_p001_figure_01` 执行一次报告，记录 Dify execution request ID、`page_count`、`image_count`、`quality_check`、warnings，并人工检查图片和图注。

## 安全与维护

- 必须设置 `PDF_TOOL_API_TOKEN` 并使用 HTTPS。
- 不要将完整机密知识库原文发送给 PDF 服务，只发送生成 PDF 所需的最终结构化内容。
- 定期清理 `output/` 中的过期文件，或改用受生命周期策略管理的对象存储。
- 若含内部数据，为 Worker/域名增加访问控制和下载鉴权。
- 保存请求、响应、`warnings` 和对应 source_id 的审计记录。
