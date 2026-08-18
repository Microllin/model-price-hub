# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
