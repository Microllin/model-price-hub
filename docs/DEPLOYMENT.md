# 部署指南

## Docker Compose（推荐）

```bash
docker compose up -d --build api catalog discovery agent
```

服务：

| 服务 | 职责 |
|---|---|
| `api` | FastAPI、前端和查询接口 |
| `catalog` | 官方模型目录调度器 |
| `discovery` | RSS/公告/页面监控调度器 |
| `agent` | OCR、截图和自动复核 Worker |
| `updater` | 手动全量价格抓取 |

手动抓取价格：

```bash
docker compose run --rm updater
```

## 健康检查

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/catalog/health
docker compose ps
```

## 后台管理

前后台分离：前台价格页 `/`，后台管理 `/admin`（需登录）。

- 生产环境务必设置强密码 `MPH_ADMIN_PASSWORD`，并在反向代理后开启 `MPH_ADMIN_COOKIE_SECURE=true`。
- 后台可管理：运行策略、官方数据源、Webhook、API Key 与管理员密码。
- 开启策略 `api_key_required` 后，`/v1/prices` 等只读接口必须携带 `X-API-Key`。
- 管理员账号数据存于 `data/admin.json`（PBKDF2 加盐哈希），会话存于 `data/admin-sessions.json`。

## 环境变量

| 变量 | 说明 |
|---|---|
| `MPH_ADMIN_USERNAME` | 后台管理员账号（默认 `admin`） |
| `MPH_ADMIN_PASSWORD` | 后台管理员初始密码；不设置则禁用后台登录 |
| `MPH_ADMIN_COOKIE_SECURE` | HTTPS 部署时置 `true`，Cookie 仅走加密连接 |
| `MPH_HTTP_PROXY` | 可选出站代理 |
| `MPH_USE_PLAYWRIGHT` | 是否启用浏览器渲染 |
| `MPH_RENDER_CONCURRENCY` | 浏览器并发上限 |
| `MPH_SCHEDULE_INTERVAL_DAYS` | 价格调度间隔 |
| `MPH_ANTHROPIC_API_KEY` | 视觉模型凭据 |
| `ANTHROPIC_AUTH_TOKEN` | 兼容视觉模型凭据 |
| `ANTHROPIC_BASE_URL` | 视觉模型网关 |
| `MPH_SEARXNG_URL` | SearXNG 地址 |

## 数据目录

生产部署建议挂载：

```text
./data:/app/data
```

## 备份

至少备份：

```text
data/prices.db
data/catalog.db
data/discovery.db
data/sources.json
data/snapshots/
data/latest.json
```

Webhook 配置包含通知端点，也应纳入受控备份。

## 升级

```bash
git pull
docker compose build
docker compose up -d
```

升级前建议先备份 `data/`。
