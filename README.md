# Model Price Hub

每日聚合**主流大模型厂商前沿主力模型**的价格,同时给出**国内 CNY** 与**国外 USD** 两种定价,以 **Web API** 形式对外提供。定位类似 litellm 的价格数据层,但补齐了 CNY、自动抓取管线与查询服务。

## 特性

- **七个全自动数据源**(每日更新):
  - **DeepSeek 官网**静态表 → 国内官方,原生 **CNY + USD**。
  - **智谱 GLM 官网**(Playwright)→ 智谱**官方 CNY** per-token 价(GLM-5.1 / GLM-5 / GLM-4.7 等)。
  - **MiniMax 官网**(Playwright)→ MiniMax**官方 CNY** per-token 价(M3 / M2.7 等)。
  - **LiteLLM 定价 JSON** → 国外主流(OpenAI / Anthropic / Google / xAI / Mistral)+ **云托管平台**(Bedrock / Azure / Vertex),USD。
  - **OpenRouter `/api/v1/models`** → 大幅补充**国内模型覆盖**(~90 个),USD。
  - **SiliconFlow(硅基流动)**(Playwright)→ 国内模型 CNY(第三方托管)。
  - **PPIO(派欧云)**(Playwright)→ 国内模型 CNY(第三方托管,交叉印证)。
- **多源交叉验证置信度**:对每个模型 ID 的**官方价**,统计有多少个独立数据源印证一致 → 高(≥3源) / 中(2源) / 低(1源);官方源之间分歧则标「待核」。非官方渠道价不计置信度,但作为旁证参与验证。
- **人工 override 层**(`data/overrides.yaml`)→ 补厂商官方直连渠道(阿里云百炼 / 火山方舟)的 CNY。
- **校验 + 变更检测**:空结果不覆盖、异常值丢弃、单价变动超阈值自动**冻结旧值**待人工复核。
- **每日快照入 git** → 免费历史 / 审计 / 兜底。
- **FastAPI + 单页 Web UI**,三 tab:**官方价(带置信度)/ 非官方渠道价 / 全部**,以模型 ID 为第一维,支持过滤与近似汇率换算。

> ⚠️ 现实约束:国内厂商官方控制台价常需登录或做了反爬(如智谱把字段名混淆)。因此国内 CNY 主要经**硅基流动 / 派欧云等三方托管平台**自动抓取(价格与官方直连可能略有差异);官方直连价保留在 override 层待人工核对。

> 🌐 **抓取方式**:DeepSeek / LiteLLM / OpenRouter 走纯 HTTP;SiliconFlow / PPIO 走 Playwright 渲染(需 `MPH_USE_PLAYWRIGHT=1` + chromium)。

## 官方定价的"一劳永逸"路径:视觉提取(官方主源)

**架构:官方定价以【视觉】为主源,【正则】抓取器降为验证器。** 两者同 `provider/channel(official)` 但 `source` 不同(视觉源前缀 `vision-`),在置信度聚合里互为印证而非互相覆盖(唯一键已含 `source`)。官方价显示值**优先取视觉**,正则/三方作为印证源计入置信度;视觉与正则分歧超容差标 `conflict`;无视觉凭据/页面不渲染时正则兜底(优雅降级)。实测:MiniMax `M2.7` 视觉值 `2.1/8.4` 被正则+PPIO 印证 → **高置信**;视觉多抓到正则漏掉的 `M3`(单源→低置信,如实标注)。**保留为正则官方源**:DeepSeek(静态表)、通义千问(SSR 大页 49 模型)。

各厂商官方定价页结构千差万别,有的价格用特殊组件/canvas 渲染,`inner_text` 读不到(如 **Kimi**)。为此提供一条通用的**视觉提取**路径,把"每家一套解析正则"收敛成一条管线:

```
Playwright 渲染(可点开各模型 tab / 裁剪定价区)→ 截图
  → Claude 多模态(默认 Haiku 4.5)在结构化输出约束下吐 JSON
    → 单位归一(元/千 ↔ 元/百万)→ RawPrice
      → 多源交叉验证兜住模型偶发看错的数字
```

- **加新官方厂商只需**:继承 `app/scrapers/vision_base.py:VisionScraper`,给一个 `source_url`(+ 可选 `tab_selectors` 点哪些 tab)。无需再写解析代码。见 `app/scrapers/kimi.py`。
- **鲁棒**:视觉读像素,不依赖 DOM,对页面改版/反爬更耐受;能读到 `inner_text` 读不到的东西。
- **启用**:`pip install ".[vision]" && playwright install chromium`,设 `MPH_USE_PLAYWRIGHT=1` 和凭据(下)。缺凭据时视觉抓取器优雅跳过,不影响其它源。
- **凭据**:支持官方 `MPH_ANTHROPIC_API_KEY`,或标准 `ANTHROPIC_AUTH_TOKEN` + 自定义网关 `ANTHROPIC_BASE_URL`(如 CloudRouter,走 Bearer token)。
- **模型档**:默认 `claude-sonnet-4-6`;可 `MPH_VISION_MODEL=...` 覆盖(取决于你的网关支持哪些模型)。
- **已验证**:对 MiniMax 官方定价页视觉提取,`MiniMax-M2.7` 得 `2.1/8.4` **与正则抓取器完全一致**,且**多抓到了正则因 rowspan 漏掉的 `MiniMax-M3`** —— 证明视觉路径既准又比 bespoke 正则更全,现有正则数据可作交叉验证。
- **抗幻觉**:提示词强约束"没看见定价表就返回空、严禁用先验知识";无定价表的页面实测返回 0 条。
- **已知限制**:少数厂商(如 **Kimi**)的价格表组件在无头 Chromium 里不渲染(无 XHR、无像素),视觉也读不了没渲染的东西 —— 需有头浏览器或等其修复 SSR。

单独试跑某厂商视觉抓取(以自定义网关为例):

```bash
export ANTHROPIC_BASE_URL="https://your-gateway" ANTHROPIC_AUTH_TOKEN="sk-..."
MPH_USE_PLAYWRIGHT=1 MPH_VISION_MODEL=claude-sonnet-4-6 \
  .venv/bin/python -c "import asyncio; from app.scrapers.kimi import KimiScraper; \
  print(asyncio.run(KimiScraper().fetch()))"
```

## 官方价 vs 非官方价 vs 置信度

- **官方渠道**(`official=true`):模型原厂自己定价的渠道 —— `official` / `aliyun-bailian` / `volcengine`。只有官方价计算置信度。
- **非官方渠道**:第三方托管/聚合平台(`siliconflow` / `ppio` / `openrouter` / `bedrock` / `azure` / `vertex`),各自定价,不计置信度。
- **置信度**:对 `(canonical_model, region, currency)`,官方价取官方源中位数;印证源 = 全部来源中价格落在 ±15% 内的去重数据源数;高=≥3、中=2、低=1;官方源互相冲突→标 `conflict`(归为中,待核)。
- **canonical_model**:各来源模型名(`deepseek-v4-pro` / `deepseek/deepseek-v4-pro` / `deepseek-ai/DeepSeek-V4-Pro`)归一后的 id,是跨源验证与前端第一筛选维度。

## 数据模型

一条价格由复合键唯一标识:`(provider, channel, model, region, currency)`。所有单价归一到**每 1M tokens**。同一模型可有多条(不同渠道/货币),例如 DeepSeek → `(deepseek, official, cn, CNY)` + `(deepseek, official, intl, USD)`。

## 快速开始

```bash
cd model-price-hub
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# 跑一次抓取(生成 data/snapshots/<date>.json + 入库 SQLite)
python -m app.pipeline.runner
#   --dry-run  只抓取+校验,不写库/快照

# 起 API
uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000/docs
```

## API

| 端点 | 说明 |
|---|---|
| `GET /health` | 健康检查 + 数据日期 |
| `GET /v1/prices` | 过滤:`provider` `channel` `model` `region` `currency`;`convert=CNY\|USD` 近似换算 |
| `GET /v1/prices/{provider}/{model}` | 某模型全部渠道/货币变体 |
| `GET /v1/providers` | 厂商 + 渠道清单 |
| `GET /v1/models` | 模型清单及其可用渠道/货币 |
| `GET /v1/snapshots` · `/v1/snapshots/{date}` | 历史快照 |

示例:

```bash
curl 'localhost:8000/v1/prices?provider=deepseek'          # CNY + USD 两套
curl 'localhost:8000/v1/prices?currency=CNY'               # 只看国内价
curl 'localhost:8000/v1/prices?channel=bedrock'            # 云托管
curl 'localhost:8000/v1/prices?provider=openai&convert=CNY'# 美元价近似换成人民币
```

## 定时更新(每 3 天一次)

价格变动不频繁、视觉入库较慢,故默认 **3 天跑一次**。两种方式任选:

1. **GitHub Action(推荐)**:`.github/workflows/price-update.yml` 每 3 天跑管线并把新快照提交回仓库。视觉主源需在仓库 Secrets 配 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`(缺失则视觉源跳过、正则兜底)。
2. **容器内调度**:`docker compose up`(含 `scheduler` 服务,APScheduler 每 3 天更新,`MPH_SCHEDULE_INTERVAL_DAYS` 可调)。

也可用宿主 cron / k8s CronJob 直接调 `python -m app.pipeline.runner`。

## 扩展新厂商

在 `app/scrapers/` 新增一个 `BaseScraper` 子类,实现 `parse(text) -> list[RawPrice]`(与网络分离,便于用 fixture 单测),然后在 `app/scrapers/registry.py` 登记。JS 重渲染页可设 `requires_render=True` 走 Playwright(`pip install ".[render]" && playwright install chromium`,并置 `MPH_USE_PLAYWRIGHT=1`)。抓不到的厂商直接写进 `data/overrides.yaml`。

## 配置

环境变量(前缀 `MPH_`)或 `.env`,见 `app/config.py`。常用:`MPH_USD_TO_CNY`(换算汇率)、`MPH_PRICE_CHANGE_FREEZE_RATIO`(冻结阈值,默认 0.40)、`MPH_SCHEDULE_HOUR/MINUTE`。

## 测试

```bash
pytest        # 解析用离线 fixture,不打网络
```

## 目录

```
app/
  config.py            全局配置
  models/pricing.py    RawPrice / PriceEntry / SQLAlchemy 表
  db/session.py        SQLite + upsert
  scrapers/            base + registry + deepseek + litellm_json
  pipeline/            store(快照/override) + validate(校验冻结) + runner(编排/CLI)
  api/                 repository + prices + snapshots + main(FastAPI)
  scheduler.py         APScheduler 定时调度(每 3 天)
data/
  snapshots/*.json     每日快照(入 git)
  latest.json          最新快照(入 git,API 兜底)
  overrides.yaml       人工 override 层
```
