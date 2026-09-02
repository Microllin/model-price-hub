# 价格监控中心 — 数据库架构设计 v2

## 设计目标

基于 22 个 scraper 的实际产出数据驱动设计，替代当前「一张大宽表」方案。

## 核心发现

从 2657 条快照数据 + scraper 离线解析可知：

| 维度 | 数据事实 |
|---|---|
| **分裂原因** | 同一 (provider,model) 出现多条的原因：**channel** (167), **source** (69), **service_tier** (24), **context_range+region+currency** (21) |
| **计费单位** | 快照全部为 `token`；但新版 scraper 已有 `request/second/10k_chars/per_image/per_voice/minute/gb_hour/10k_chats` 共 9 种 |
| **模态** | 快照几乎全 `text`；新版已有 `multimodal/tts/video/image/mcp/search/storage/training/finetune-inference` |
| **service_tier** | `standard`(默认) / `batch` / `flex` / `priority` / `low_latency` / `scheduled` / `fast` / `lora` / `full` |
| **价格字段** | input_per_1m(99%), output_per_1m(99%), cached_input_per_1m(34%), cache_write_per_1m(11%) |
| **区域/货币** | `cn/CNY` 和 `intl/USD`，强绑定（CN=CNY, INTL=USD） |

## ER 架构（4 表）

```
┌─────────────────────┐
│     providers       │  厂商/渠道
├─────────────────────┤
│ id            PK    │
│ slug          UQ    │  "deepseek" / "openrouter"
│ display_name        │  "DeepSeek" / "OpenRouter"
│ type          ENUM  │  vendor / reseller / aggregator
│ homepage_url        │
│ pricing_url         │  官方定价页 URL
│ region        ENUM  │  cn / intl / both
│ is_active     BOOL  │
│ created_at          │
│ updated_at          │
└────────┬────────────┘
         │ 1
         │
         │ N
┌────────┴────────────┐
│      models         │  模型目录（逻辑模型）
├─────────────────────┤
│ id            PK    │
│ provider_id   FK    │  → providers.id
│ model_id      STR   │  API 调用用的 model name
│ display_name        │  展示名
│ canonical     STR   │  归一化名（跨渠道比价键）
│ modality      ENUM  │  text / multimodal / image / video / tts / asr / embedding / rerank / search / storage / training
│ context_window INT  │  最大上下文 token 数
│ max_output    INT   │  最大输出 token 数
│ is_active     BOOL  │
│ release_date  DATE  │
│ sunset_date   DATE  │  下线日期
│ created_at          │
│ updated_at          │
│ UNIQUE(provider_id, model_id)
└────────┬────────────┘
         │ 1
         │
         │ N
┌────────┴────────────┐
│   price_plans       │  定价方案（同模型可有多个方案）
├─────────────────────┤
│ id            PK    │
│ model_id      FK    │  → models.id
│ channel       STR   │  "official" / "openrouter" / "siliconflow" ...
│ currency      ENUM  │  CNY / USD
│ region        ENUM  │  cn / intl
│ billing_unit  ENUM  │  token / request / second / minute / 10k_chars / per_image / per_voice / gb_hour / 10k_chats
│ service_tier  STR   │  "standard" / "batch" / "flex" / "fast" / "scheduled" / "lora" / "full" ...
│ context_range STR   │  "0-32k" / ">32k" / "in:0-32k+out:>0.2k" / NULL
│ time_window   JSON  │  {"label":"off-peak","windows":[...]} / NULL
│ -- 价格字段 --      │
│ input_price   DEC   │  输入单价（单位由 billing_unit 决定含义）
│ output_price  DEC   │  输出单价（NULL 表示统一价或不适用）
│ cached_read   DEC   │  缓存命中读价（NULL=不支持）
│ cached_write  DEC   │  缓存写入价（NULL=不支持）
│ -- 元信息 --        │
│ source        STR   │  数据来源标识 "zhipu" / "vision-zhipu" / "litellm"
│ source_url    STR   │  来源页面 URL
│ effective_from DT   │  生效时间
│ effective_to  DT    │  失效时间（NULL=当前有效）
│ scraped_at    DT    │  抓取时间
│ is_current    BOOL  │  是否当前生效（同维度仅一条 TRUE）
│ created_at          │
│ updated_at          │
│ UNIQUE(model_id, channel, currency, service_tier, context_range, source, billing_unit, time_window_hash)
└────────┬────────────┘
         │ N
         │
         │ 1
┌────────┴────────────┐
│  scrape_runs        │  抓取运行记录
├─────────────────────┤
│ id            PK    │
│ run_date      DATE  │
│ started_at    DT    │
│ finished_at   DT    │
│ total_scraped INT   │  本轮抓取总条数
│ new_count     INT   │  新增
│ changed_count INT   │  价格变更
│ stale_count   INT   │  消失标记为 stale
│ error_count   INT   │  抓取失败
│ run_config    JSON  │  运行配置快照
│ created_at          │
└─────────────────────┘
```

## 与旧表的映射

| 旧字段 | 新位置 | 说明 |
|---|---|---|
| provider | providers.slug | 拆到独立表 |
| model | models.model_id | 拆到独立表 |
| canonical_model | models.canonical | 归一化名 |
| channel | price_plans.channel | 不变 |
| region/currency | price_plans.region/currency | 不变 |
| input_per_1m | price_plans.input_price | 重命名，语义由 billing_unit 决定 |
| output_per_1m | price_plans.output_price | 同上 |
| cached_input_per_1m | price_plans.cached_read | 缩短名 |
| cache_write_per_1m | price_plans.cached_write | 缩短名 |
| context_window | models.context_window | 上提到模型级 |
| max_output | models.max_output | 上提到模型级 |
| service_tier | price_plans.service_tier | 不变 |
| modality | models.modality | 上提到模型级 |
| billing_unit | price_plans.billing_unit | 不变 |
| context_range | price_plans.context_range | 不变 |
| time_window | price_plans.time_window | 不变 |
| source | price_plans.source | 不变 |
| source_url | price_plans.source_url | 不变 |
| official | 由 channel="official" 推导 | 去掉冗余字段 |
| provenance | 由 is_current + effective_to 推导 | 去掉，用时序表达 |
| condition_key | 由复合唯一键替代 | 去掉冗余 JSON |

## 设计决策

### 1. 为什么拆 providers + models？
- **providers** 22 个 scraper 对应约 90+ provider，需要存储 type/url/region 等元信息
- **models** 同一 provider 下有几十上百个模型，context_window/modality 是模型级属性不随价格变化
- 拆开后 `price_plans` 表更纯粹：只存价格维度

### 2. 为什么不拆 price_history？
- 当前阶段用 `is_current` + `effective_to` 做软过期即可
- 每日快照 JSON 文件已有完整历史（2657 条/天），DB 不需要存全量历史
- 未来需要时加一张 `price_changes` 审计表即可

### 3. billing_unit 如何统一？
- `token` 类型：input_price/output_price 单位为「元/百万tokens」或「$/M tokens」
- `request` / `per_image`：input_price = 单次价格，output_price = NULL
- `second`：input_price = 元/秒
- `10k_chars`：input_price = 元/万字符
- `minute`：input_price = 元/分钟
- `gb_hour`：input_price = 元/GB/小时
- 不做单位转换，保持原始计费方式，前端按 billing_unit 选择展示格式

### 4. ENUM 还是 STRING？
- `region` / `currency` / `billing_unit` 用 ENUM（值集合小且稳定）
- `channel` / `service_tier` / `source` 用 STRING（值随 scraper 增长）
- `modality` 用 STRING（新模态在快速增加）
