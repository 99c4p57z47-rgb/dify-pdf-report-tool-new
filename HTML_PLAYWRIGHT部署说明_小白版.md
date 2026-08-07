# HTML/CSS + Playwright PDF 样式升级

这次更新只替换 PDF 的排版与渲染方式，不改变现有调用链：

`Dify → Cloudflare Worker → Railway → PDF 下载链接`

Cloudflare Worker 地址、Dify 工具地址、鉴权 Token 和 OpenAPI Schema 都不需要修改。

## 一、需要上传到 GitHub 的文件

将压缩包中的文件按原目录覆盖到仓库根目录：

```text
Dockerfile
requirements.txt
app/main.py
app/html_renderer.py
app/report_view.py
app/templates/report.html
app/templates/components/chart.html
app/templates/components/cover.html
app/templates/components/figure.html
app/templates/components/section.html
app/templates/components/sources.html
app/templates/components/table.html
app/templates/components/toc.html
app/templates/static/report.css
app/templates/static/texture.svg
```

不要删除仓库原有的 `assets`、`knowledge`、`fonts`、`app/charts.py` 或其他文件。

## 二、在 GitHub 网页上传

1. 打开当前连接 Railway 的 GitHub 仓库。
2. 点击 **Add file → Upload files**。
3. 将解压后的文件夹内容拖入上传区域，并保持上面的目录结构。
4. 如果 GitHub 提示同名文件，将它们覆盖。
5. 在提交说明中填写：`升级 HTML Playwright PDF 排版`。
6. 点击 **Commit changes**。

如果网页一次不能上传整个目录，就依次进入 `app`、`app/templates`、`components`、`static` 后上传对应文件。

## 三、等待 Railway 自动部署

1. 打开 Railway 项目。
2. 进入 PDF 服务的 **Deployments**。
3. 等待最新部署变为绿色 `SUCCESS`。
4. 第一次构建会下载 Playwright 运行环境，通常比以前多花几分钟。

无需修改 Railway 的启动命令，也无需运行 `playwright install`；Docker 镜像已经包含 Chromium。

## 四、检查是否成功

浏览器打开：

```text
https://你的Railway域名/health
```

正确结果应包含：

```json
{
  "status": "ok",
  "version": "1.2.0",
  "renderer": "html-playwright",
  "renderer_ready": true
}
```

如果 `renderer_ready` 为 `false`，打开 Railway 的部署日志，搜索 `HTML PDF renderer failed to start`。

## 五、在 Dify 测试

继续使用原来的 `create_industry_pdf` 工具，发送：

```text
请基于知识库生成一份《2026年中国家纺行业趋势报告》PDF，包含市场、消费、色彩材质、产品机会和行动建议，并使用服务器图片和图表。
```

预期效果：

- 封面为浓背景的商业报告风格；
- 正文使用极淡纹理；
- 数据图表位于纯白高对比卡片中；
- 原有 Matplotlib 图表生成器继续使用；
- 图片保持比例，不拉伸；
- 表格表头清晰，正文不重叠；
- 下载链接和原来的 Cloudflare 地址保持不变。

## 六、出现问题时回退

在 GitHub 打开本次提交，点击 **Revert**。Railway 会自动重新部署上一版，Cloudflare 和 Dify 仍然无需修改。
