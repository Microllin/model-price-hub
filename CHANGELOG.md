# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.6.1] - 2026-08-19

### Added

- 新增火山引擎（字节/豆包）官方抓取器：Playwright 渲染 SPA 定价页，解析 19 个模型× 3 档位 × 多分档 = 117 条精确条目。
  - doubao-seed-evolving / 2.1-pro / 2.1-turbo / 2.0-pro / 2.0-lite / 2.0-mini / 2.0-code / 1.8 / 1.6 / 1.6-flash / 1.6-lite 等全系
  - 服务档位：在线常规(standard) / 在线低延迟(low_latency) / 批量推理(batch)
  - 上下文分档：[0,32k] / (32k,128k] / (128k,256k] 等（单位千token，归一为 k）
  - 输入/输出/缓存命中（非音频）三个价格字段
  - 视频(seedance)、图片(seedream)、embedding、精调、TPM 保障包等非 per-token 表正确跳过
- 新增 3 个离线 fixture 测试，总计 79 个测试通过。

## [0.6.0] - 2026-08-19

### Added

- LiteLLM 抓取器取消家族白名单过滤，全量纳入所有 chat/responses 模型（401 → 1973 条）。
- LiteLLM `_CHANNEL_MAP` 从 10 家扩展到 60+ 家：新增火山引擎/volcengine、阿里/dashscope、月之暗面/moonshot、Groq、Fireworks、Together、Deepinfra、Perplexity、Cerebras、Replicate 等。
- OpenRouter `_VENDOR_MAP` 新增 bytedance-seed、xiaomi、kuaishou、nvidia、perplexity、amazon、poolside、inclusionai 等 20+ 厂商（136 → 232 条）。
- 未识别的 vendor 不再丢弃，回退为用 vendor 前缀作为 provider。

### Changed

- 总条目数从 893 增至 **2557**（+186%），覆盖 **93 家厂商**。
- 新增模型 **882 个**，含 bytedance/seed 系列6条、aliyun/qwen 系 369 条、meta/llama 系 264 条、fireworks 65 条、perplexity 25 条等。

## [0.5.1] - 2026-08-19

### Changed

- 主题切换为靖蓝紫 AI 平台风格（参考 NexusAI）：
  - 暗色：深靖蓝黑底 `#0A0E1A` + Indigo `#818CF8` 品牌色
  - 亮色：薰衣草白底 `#F7F8FC` + Indigo `#6366F1`
  - 字体切换为 DM Sans + Space Grotesk（数字列专用等宽字体）
  - logo 渐变、tab 活动态阴影、标签徽章色系全部对齐紫色调

## [0.5.0] - 2026-08-19

### Changed

- 前台页面全量重构：
  - 色系：深邓墨蓝黑暗色 + 暖纸感亮色，翠绿 Emerald 品牌色更克制精致
  - 自定义下拉组件：全部 10 个系统 select 替换为自定义浮层下拉（带搜索、选中勾、键盘关闭）
  - 表格：width: 100% + colgroup 列宽分配，表头排序指示器 ▲/▼
  - 头部/导航、徽章、按钮、滚动条、新闻卡片全系重绘
  - 响应式适配移动端

## [0.4.5] - 2026-08-18

### Changed

- 价格条件抽屉与主表列对齐：展开行直接复用主表列结构（同一表格渲染，价格/置信度/来源逐列对齐），条件维度（档位/模态/缓存/上下文档位/时段）以徽章收进首列。
- 抽屉只显示主行之外的条件，不再重复主行数据。
- 主行代表价带条件时（如纯分档/分时段模型），模型名后标注主行条件（如「分时段」「0-128k」）。
- 条件徽章精简：standard 档位在有其他条件时不再冗余显示。

## [0.4.4] - 2026-08-18

### Changed

- 官方价格表改为按模型聚合：16 列精简为 10 列，价格列无需横向滚动即可见（1440px 视口零溢出）。
- 每个模型一行主行（标准价 + 置信度 + 数据来源），点击展开全部价格条件明细（服务档位/模态/缓存状态/上下文档位/峰谷时段徽章 + 分项价格 + 来源链接）。
- 计数文案改为「N 个模型 · M 条价格条件」。

## [0.4.3] - 2026-08-18

### Fixed

- 修复官方价格聚合丢弃「仅缓存写」条件价行的问题（Anthropic write_5m/write_1h 行现在官方价格表可见）。

## [0.4.2] - 2026-08-18

### Added

- 官方价格表新增「数据来源」列：显示价主源名称 + 官方来源链接，每行可溯源。

### Fixed

- 修复 `/v1/official-prices` 聚合行丢失字段的问题：`source_url`、`cached_input_per_1m`、`cache_write_per_1m`、`scraped_at` 现正确输出（此前官方 tab 缓存读/写两列始终为空）。

## [0.4.1] - 2026-08-18

### Fixed

- 修复校验层 `_sane` 把「仅缓存写价格」的条件价行当空数据丢弃的问题（Anthropic cache write 5m/1h 维度曾整批丢失，30 条）。

## [0.4.0] - 2026-08-18

### Added

- 新增 Anthropic 官方抓取器：主表（输入/输出/缓存读）+ 缓存写 5m/1h 双档条件价（`cache_state=write_5m/write_1h`）+ Fast mode 与 Batch 档位。
- 新增 OpenAI 官方抓取器：standard / batch / flex / fast 四个服务档位（从 astro-island props 提取），短/长上下文双列（`context_range=long`），Realtime 模态分项（audio/text/image），ChatGPT/Codex 分类表。
- 新增 Google 官方抓取器：27 个 Gemini/Gemma 模型 × standard/batch/flex/priority 四档，上下文分档（0-200k/>200k），促销价取当前生效价并记录 `effective_to`，存储费与 grounding 按次计费正确剔除。
- 三个抓取器均为 plain HTTP（页面 SSR 友好，无需 Playwright/视觉凭据），模型 id 对齐 litellm 风格，与 LiteLLM 旁证自动交叉验证。
- `extract_tables_with_context`：表格抽取携带 h1-h4 标题路径。
- 新增 9 个离线 fixture 测试（三家官方源）。

### Changed

- Webhook 官方源集合新增 openai / anthropic / google，其官方价格事件可正常投递。

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
