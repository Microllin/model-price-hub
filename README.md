# Model Price Hub

[![CI](https://github.com/Microllin/model-price-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/Microllin/model-price-hub/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Microllin/model-price-hub)](https://github.com/Microllin/model-price-hub/releases)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/Microllin/model-price-hub)](LICENSE)

大模型官方价格、模型生命周期和变更监控平台。

Model Price Hub 将官方模型目录、官方价格、第三方价格旁证、RSS/公告监控和 Agent 自动复核拆成清晰的数据边界，提供 FastAPI、Web 前端、Docker 调度服务和 Webhook 通知。

## 核心能力

- **官方模型目录**：从厂商官方模型页建立完整模型清单，自动识别上线和下线。
- **官方价格**：解析官方价格页，支持输入/输出、缓存、服务档位、模态、上下文范围、峰谷时段和生效时间。
- **第三方旁证**：OpenRouter、LiteLLM、SiliconFlow、PPIO 等渠道仅作为价格旁证，不参与官方结论。
- **监控发现**：RSS、公告、搜索和官方页面变化进入 Discovery，不直接污染正式数据。
- **Agent 自动复核**：官方页面异常时自动截图、渲染和 OCR，无需人工点击确认。
- **Webhook**：支持飞书 Interactive Card 和标准 JSON Webhook。
- **Docker 部署**：API、Catalog、Discovery、Agent 和 SearXNG 分服务运行并带健康检查。

## 架构

```text
Browser UI
  └─ FastAPI
      ├─ Catalog Service       官方模型目录与生命周期
      ├─ Pricing Service       官方/第三方价格抓取与快照
      ├─ Discovery Service     RSS、公告、搜索和页面监控
      ├─ Agent Worker          OCR、截图、页面差异和上下文复核
      └─ Config Service        来源、Webhook、代理和调度配置
```

详细设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python ≥ 3.10 |
| Web 框架 | FastAPI + Uvicorn |
| 存储 | SQLite（SQLAlchemy 2.x）+ JSON 快照 |
| 抓取 | httpx + selectolax；动态页面用 Playwright (Chromium) |
| 视觉提取 | Anthropic 多模态模型（截图 → 结构化 JSON，可选） |
| 调度 | APScheduler / Docker Compose / GitHub Actions |
| 前端 | 原生 HTML/JS 单页（`app/web/index.html`） |
| 部署 | Docker + Docker Compose；可选自托管 SearXNG |

## 版本要求

- **Python**：≥ 3.10（CI 使用 3.12 验证）
- **Docker**：Docker Engine ≥ 20.10 且 Compose v2（`docker compose`）
- **Node.js**：不需要，前端无构建步骤
- **内存**：API 服务约 1 GB；启用 Playwright 渲染的调度服务建议 ≥ 4 GB
- **浏览器依赖**：仅启用渲染/视觉提取时需要 `python -m playwright install chromium`

## 快速开始

### Docker

```bash
docker compose up -d --build api catalog discovery agent
```

打开：

```text
http://127.0.0.1:8000/
```

手动全量抓取价格：

```bash
docker compose run --rm updater
```

### 本地开发

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,vision]"
python -m playwright install chromium
pytest
uvicorn app.main:app --reload
```

## API

| 端点 | 说明 |
|---|---|
| `GET /health` | 服务与价格管线健康状态 |
| `GET /v1/prices` | 价格查询与筛选 |
| `GET /v1/official-prices` | 官方价格与置信度 |
| `GET /v1/catalog` | 官方模型目录 |
| `GET /v1/catalog/health` | 模型目录健康状态 |
| `POST /v1/catalog/run` | 手动运行目录差异检测 |
| `GET /v1/discovery` | 监控候选与资讯 |
| `POST /v1/discovery/run` | 手动运行监控 |
| `GET /v1/sources` | 官方来源配置 |
| `POST /v1/sources` | 新增官方来源 |
| `POST /v1/sources/{id}/agent` | 创建 Agent 复核任务 |
| `GET /v1/webhooks` | Webhook 配置 |

## 配置

所有配置项通过环境变量（前缀 `MPH_`）或 `.env` 文件注入，完整示例见 [.env.example](.env.example)。

常用环境变量：

| 变量 | 说明 |
|---|---|
| `MPH_HTTP_PROXY` | 可选出站代理 |
| `MPH_USE_PLAYWRIGHT` | 启用浏览器渲染 |
| `MPH_RENDER_CONCURRENCY` | 浏览器并发上限 |
| `MPH_ANTHROPIC_API_KEY` | 视觉模型凭据 |
| `ANTHROPIC_AUTH_TOKEN` | 兼容视觉模型凭据 |
| `ANTHROPIC_BASE_URL` | 视觉模型网关 |
| `MPH_SEARXNG_URL` | SearXNG 地址 |

完整部署说明见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 测试与质量

```bash
pytest
python -m compileall app
git diff --check
docker compose config -q
```

## 发布

发布流程见 [docs/RELEASE.md](docs/RELEASE.md)，变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)
