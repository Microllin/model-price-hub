"""SiliconFlow 抓取器解析测试(离线渲染后文本 fixture)。"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.siliconflow import SiliconFlowScraper
from tests.conftest import read_fixture


def _index(rows):
    return {r.model: r for r in rows}


def test_parses_cny_and_maps_vendor():
    rows = SiliconFlowScraper().parse(read_fixture("siliconflow_body.txt"))
    idx = _index(rows)

    ds = idx["deepseek-ai/DeepSeek-V4-Pro"]
    assert ds.provider == "deepseek"
    assert ds.currency == Currency.CNY and ds.region == Region.CN
    assert ds.channel == "siliconflow"
    assert ds.input_per_1m == 12.0 and ds.output_per_1m == 24.0

    # zai-org → zhipu
    assert idx["zai-org/GLM-5.2"].provider == "zhipu"
    # Qwen → aliyun
    assert idx["Qwen/Qwen3.6-35B-A3B"].provider == "aliyun"
    # 多段 id:Pro/moonshotai/Kimi → 取倒数第二段 moonshotai → moonshot
    assert idx["Pro/moonshotai/Kimi-K2.6"].provider == "moonshot"


def test_skips_image_and_embedding_models():
    rows = SiliconFlowScraper().parse(read_fixture("siliconflow_body.txt"))
    models = {r.model for r in rows}
    assert "Qwen/Qwen-Image-Edit" not in models   # 图像
    assert "BAAI/bge-large-zh" not in models        # 向量/embedding
    assert len(rows) == 4
