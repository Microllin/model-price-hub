"""全局配置。所有可调项集中于此,通过环境变量(前缀 MPH_)或 .env 覆盖。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录(app/ 的上一级)
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MPH_", env_file=".env", extra="ignore"
    )

    # ---- 存储 ----
    db_path: Path = DATA_DIR / "prices.db"
    snapshots_dir: Path = DATA_DIR / "snapshots"
    latest_path: Path = DATA_DIR / "latest.json"
    overrides_path: Path = DATA_DIR / "overrides.yaml"
    pipeline_health_path: Path = DATA_DIR / "pipeline-health.json"
    webhook_config_path: Path = DATA_DIR / "webhooks.json"
    webhook_removal_state_path: Path = DATA_DIR / "webhook-removal-state.json"
    webhook_removal_confirmations: int = 2
    discovery_candidates_path: Path = DATA_DIR / "discovery-candidates.json"
    discovery_db_path: Path = DATA_DIR / "discovery.db"
    searxng_url: str | None = None
    source_config_path: Path = DATA_DIR / "sources.json"
    agent_tasks_path: Path = DATA_DIR / "agent-tasks.json"
    catalog_db_path: Path = DATA_DIR / "catalog.db"

    # ---- 后台管理 ----
    data_dir: Path = DATA_DIR
    admin_username: str = "admin"
    admin_password: str | None = None
    admin_cookie_secure: bool = False

    # ---- 抓取 ----
    http_timeout: float = 20.0
    # 可选出站代理；不设置时使用系统直连。
    http_proxy: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (compatible; ModelPriceHub/0.1; +https://example.com/model-price-hub)"
    )
    use_playwright: bool = False  # 需 `pip install .[render] && playwright install chromium`
    # 同时最多几个渲染类抓取器(各自会 launch 一个 Chromium)。默认 1:串行跑浏览器,
    # 避免 N 个 Chromium 并发把整机内存撑爆(历史峰值 ~15G / OOM)。HTTP 源不受此限,仍全并发。
    render_concurrency: int = 1
    # 管道默认严格串行；即使 HTTP 源也不同时打满连接、内存和对端。
    scraper_concurrency: int = 1
    # 每个视觉页面最多截取多少个视口。页越长，截图和视觉 API 请求越多，
    # 默认 6 足够覆盖常规定价表，也避免把整页内容堆在内存中。
    vision_max_shots_per_page: int = 6
    # 浏览器单页等待时间；避免 networkidle 被第三方长连接拖住。
    render_settle_seconds: float = 3.0

    # ---- 视觉提取(截图 → Claude 多模态 → 结构化 JSON)----
    anthropic_api_key: str | None = None      # MPH_ANTHROPIC_API_KEY;或用标准 ANTHROPIC_AUTH_TOKEN
    vision_model: str = "claude-sonnet-4-6"    # 视觉提取模型;可用 MPH_VISION_MODEL 覆盖

    # ---- 校验 ----
    # 单项价格相较上一快照变动超过该比例 → 冻结(保留旧值并标记 needs_review)
    price_change_freeze_ratio: float = 0.40

    # ---- 调度 ----
    schedule_interval_days: int = 3   # 每几天跑一次抓取管线(视觉入库较慢,默认 3 天)
    schedule_hour: int = 3
    schedule_minute: int = 17

    # ---- 汇率(仅用于 ?convert= 的近似换算,原生货币始终是 source of truth)----
    usd_to_cny: float = 7.15

    # ---- 告警(可选)----
    alert_webhook: str | None = None

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()

# 确保运行期目录存在
settings.snapshots_dir.mkdir(parents=True, exist_ok=True)
