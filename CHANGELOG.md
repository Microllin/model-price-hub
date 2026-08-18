# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-08-18

### Added

- 新增数据质量漂移报告：每次管线运行产出 `data/drift.json`，包含：
  - 新增 / 消失模型清单（对比上一快照）
  - 未匹配官方条目（仅单一来源，无法交叉验证）
  - 孤儿模型（只有第三方旁证、无官方源）
  - 跨源价格偏差告警（第三方参考价 vs 官方价 >5%，仅标准档参与对比，条件价不误报）
  - 维度覆盖率统计（cache_write / context_range / time_window 等字段填充率）
- 新增后台「数据质量」页与 `GET /v1/admin/drift` API。
- 阿里云百炼抓取器按上下文阶梯产出多条 `context_range` 记录（0-32k / 32k-128k / >128k）。
- 智谱抓取器按「输入长度 [0,32) / [32+)」分档产出。
- MiniMax 抓取器按 ≤512k / >512k 分档产出。

### Changed

- 价格维度从「定义了但抓不到」走向实际填充：context_range 覆盖率从 0% 提升。

## [0.2.1] - 2026-08-18

### Added

- 前后台分离：新增独立后台页面 `/admin`，前台价格页不再内嵌管理弹窗。
- 后台六个管理模块：概览（服务/管线/目录健康）、策略配置、数据源、Webhook、API Key、账号。
- 登录防爆破：同一账号连续失败 5 次锁定 10 分钟。
- 策略配置真正生效：
  - `price_change_freeze_ratio` 实时作用于价格校验管线；
  - `notify_price_changes` / `notify_model_lifecycle` / `notify_third_party` 实时作用于 Webhook 事件过滤。
- 新增策略 `api_key_required`：开启后只读查询 API 必须携带 `X-API-Key`。
- API Key 支持启用/停用、每分钟限流和最近使用时间追踪。
- 后台支持修改管理员密码（更新后全部会话失效）。

### Changed

- 策略模型统一收敛到 `app/policy.py`，后台保存后无需重启即被管线与 Webhook 消费。
- 前台价格页右上角管理入口改为跳转 `/admin` 独立页面。

## [0.2.0] - 2026-08-18

### Added

- 新增四层监控架构：
  - 官方模型目录层（Catalog）
  - 官方价格层（Pricing）
  - 监控与 Discovery 层
  - 配置与 Agent 层
- 新增官方模型目录快照、生命周期差异检测和 `/v1/catalog` API。
- 新增官方来源配置 API `/v1/sources` 与前端配置入口。
- 新增 Agent 任务队列、OCR/截图复核 Worker 和 `/v1/sources/tasks`。
- 新增 Webhook 管理、飞书 Interactive Card 与官方事件过滤。
- 新增 Discovery SQLite 持久化、SearXNG 搜索和来源分层。
- 新增 DeepSeek Peak / Off-Peak 多维价格、时区和生效时间。
- 新增 Docker API、Catalog、Discovery、Agent 服务与健康检查。

### Changed

- 模型上线/下线仅由官方模型目录决定，价格层不再推断生命周期。
- 官方价格变化自动执行；异常官方页面由 Agent 自动截图/OCR 复核。
- 第三方价格仅作为旁证，不再参与官方价格或官方通知。
- 前端价格表改为固定列宽、单行展示、横向滚动和首列冻结。
- Docker 镜像升级到 Playwright Python v1.62.0。

### Fixed

- 修复 LiteLLM 被误判为官方来源的问题。
- 修复官方价格聚合丢失时间窗口和生效时间的问题。
- 修复 DeepSeek 峰谷价格解析和数据库唯一键冲突。
- 修复价格页面纵向滚动被禁用的问题。
- 修复前端脚本因缺少 `windowText` 导致无数据的问题。

## [0.1.0] - 2026-07-16

### Added

- 初始价格抓取、SQLite 存储、FastAPI 查询接口和基础 Web UI。
- DeepSeek、智谱、MiniMax、LiteLLM、OpenRouter、SiliconFlow、PPIO 数据源。
- 快照、override、置信度聚合和定时更新工作流。
