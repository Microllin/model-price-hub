"""智谱 GLM / MiniMax 官网抓取器解析测试（离线渲染后文本 fixture）。

覆盖场景（智谱 — 全量 API 定价）：
- 旗舰文本区：新品/无阶梯/输入阶梯/输入+输出双维阶梯/免费模型/缓存价
- 旗舰视觉区：GLM-5V-Turbo / GLM-4.6V / GLM-4.6V-FlashX / GLM-4.6V-Flash / GLM-4.5V
- 推理区：Language Models / Reasoning / Multimodal(request) / Real-time(chats/minute) / Embedding / More
- 搜索工具服务（按次）
- 知识库扩容（GB/hour）
- 微调 Training（LoRA + Full，含 Not Supported 跳过）
- 微调 Inference（Public Instance 价格，排除 GPU Unit / Day）
- 全局一致性 + 总数
"""
from __future__ import annotations

from app.models.pricing import Currency, Region
from app.scrapers.minimax import MiniMaxScraper
from app.scrapers.zhipu import ZhipuScraper
from tests.conftest import read_fixture


# ===================================================================
#  Helper
# ===================================================================

def _zhipu_parse():
    return ZhipuScraper().parse(read_fixture("zhipu_body.txt"))


def _zhipu_by_model():
    by: dict[str, list] = {}
    for r in _zhipu_parse():
        by.setdefault(r.model, []).append(r)
    return by


def _zhipu_by_modality():
    by: dict[str, list] = {}
    for r in _zhipu_parse():
        by.setdefault(r.modality, []).append(r)
    return by


# ===================================================================
#  1. 旗舰 — 文本模型 tab
# ===================================================================

def test_glm53_flagship_new():
    by = _zhipu_by_model()
    r = by["GLM-5.3"][0]
    assert r.input_per_1m == 8.0 and r.output_per_1m == 28.0
    assert r.cached_input_per_1m == 2.0
    assert r.context_window == 1_000_000
    assert r.context_range is None
    assert r.modality == "text"


def test_glm52_no_tier():
    r = _zhipu_by_model()["GLM-5.2"][0]
    assert r.input_per_1m == 8.0 and r.output_per_1m == 28.0
    assert r.cached_input_per_1m == 2.0


def test_glm51_two_input_tiers():
    tiers = {r.context_range: r for r in _zhipu_by_model()["GLM-5.1"]}
    assert set(tiers.keys()) == {"0-32k", ">32k"}
    assert tiers["0-32k"].input_per_1m == 6.0 and tiers["0-32k"].output_per_1m == 24.0
    assert tiers["0-32k"].cached_input_per_1m == 1.3
    assert tiers[">32k"].input_per_1m == 8.0 and tiers[">32k"].output_per_1m == 28.0


def test_glm5_turbo_two_tiers():
    tiers = {r.context_range: r for r in _zhipu_by_model()["GLM-5-Turbo"]}
    assert tiers["0-32k"].input_per_1m == 5.0 and tiers["0-32k"].cached_input_per_1m == 1.2
    assert tiers[">32k"].input_per_1m == 7.0


def test_glm5_two_tiers():
    tiers = {r.context_range: r for r in _zhipu_by_model()["GLM-5"]}
    assert tiers["0-32k"].input_per_1m == 4.0 and tiers["0-32k"].output_per_1m == 18.0
    assert tiers[">32k"].input_per_1m == 6.0


def test_glm47_dual_dimension_tiers():
    tiers = {r.context_range: r for r in _zhipu_by_model()["GLM-4.7"]
             if r.modality == "text"}
    assert len(tiers) == 3
    assert tiers["in:0-32k+out:0-0.2k"].input_per_1m == 2.0
    assert tiers["in:0-32k+out:>0.2k"].input_per_1m == 3.0
    assert tiers["32-200k"].input_per_1m == 4.0


def test_glm45_air_dual_dimension_tiers():
    tiers = {r.context_range: r for r in _zhipu_by_model()["GLM-4.5-Air"]
             if r.modality == "text"}
    assert len(tiers) == 3
    assert tiers["in:0-32k+out:0-0.2k"].input_per_1m == 0.8
    assert tiers["in:0-32k+out:>0.2k"].output_per_1m == 6.0
    assert tiers["32-128k"].cached_input_per_1m == 0.24


def test_glm47_flashx_no_tier():
    r = _zhipu_by_model()["GLM-4.7-FlashX"][0]
    assert r.input_per_1m == 0.5 and r.output_per_1m == 3.0
    assert r.context_window == 200_000


def test_glm47_flash_free():
    r = _zhipu_by_model()["GLM-4.7-Flash"][0]
    assert r.input_per_1m == 0.0 and r.output_per_1m == 0.0
    assert r.cached_input_per_1m == 0.0


# ===================================================================
#  2. 旗舰 — 视觉理解 tab
# ===================================================================

def test_glm5v_turbo_multimodal():
    rows = [r for r in _zhipu_by_model()["GLM-5V-Turbo"] if r.modality == "multimodal"]
    tiers = {r.context_range: r for r in rows}
    assert len(tiers) == 2
    assert tiers["0-32k"].input_per_1m == 5.0 and tiers["0-32k"].output_per_1m == 22.0
    assert tiers[">32k"].input_per_1m == 7.0


def test_glm46v_two_tiers():
    rows = [r for r in _zhipu_by_model()["GLM-4.6V"] if r.modality == "multimodal"]
    tiers = {r.context_range: r for r in rows}
    assert tiers["0-32k"].input_per_1m == 1.0 and tiers["0-32k"].output_per_1m == 3.0
    assert tiers["32-128k"].input_per_1m == 2.0


def test_glm46v_flashx():
    rows = [r for r in _zhipu_by_model()["GLM-4.6V-FlashX"] if r.modality == "multimodal"]
    tiers = {r.context_range: r for r in rows}
    assert tiers["0-32k"].input_per_1m == 0.15 and tiers["0-32k"].output_per_1m == 1.5
    assert tiers["32-128k"].input_per_1m == 0.3


def test_glm46v_flash_free():
    r = _zhipu_by_model()["GLM-4.6V-Flash"][0]
    assert r.modality == "multimodal"
    assert r.input_per_1m == 0.0 and r.output_per_1m == 0.0


def test_glm45v_two_tiers():
    rows = [r for r in _zhipu_by_model()["GLM-4.5V"] if r.modality == "multimodal"]
    tiers = {r.context_range: r for r in rows}
    assert tiers["0-32k"].input_per_1m == 2.0 and tiers["0-32k"].output_per_1m == 6.0
    assert tiers["32-64k"].input_per_1m == 4.0 and tiers["32-64k"].output_per_1m == 12.0


# ===================================================================
#  3. 推理区 — Language Models（默认 tab）
# ===================================================================

def test_legacy_glm4_plus():
    r = _zhipu_by_model()["GLM-4-Plus"][0]
    assert r.input_per_1m == 5.0 and r.output_per_1m == 5.0


def test_legacy_glm4_air():
    r = [r for r in _zhipu_by_model()["GLM-4-Air"] if r.modality == "text"][0]
    assert r.input_per_1m == 0.5


def test_legacy_glm4_assistant():
    r = _zhipu_by_model()["GLM-4-Assistant"][0]
    assert r.input_per_1m == 5.0


# ===================================================================
#  4. 推理区 — Reasoning models
# ===================================================================

def test_reasoning_glm_z1_air():
    r = _zhipu_by_model()["GLM-Z1-Air"][0]
    assert r.input_per_1m == 0.5 and r.output_per_1m == 0.5


def test_reasoning_glm_z1_airx():
    assert _zhipu_by_model()["GLM-Z1-AirX"][0].input_per_1m == 5.0


def test_reasoning_glm_z1_flashx():
    assert _zhipu_by_model()["GLM-Z1-FlashX"][0].input_per_1m == 0.1


def test_reasoning_glm41v_thinking():
    assert _zhipu_by_model()["GLM-4.1V-Thinking-FlashX"][0].input_per_1m == 2.0


# ===================================================================
#  5. 推理区 — Multimodal Models（含 per request 计费）
# ===================================================================

def test_multimodal_glm4v_plus():
    r = _zhipu_by_model()["GLM-4V-Plus-0111"][0]
    assert r.input_per_1m == 4.0 and r.billing_unit == "token"


def test_multimodal_cogview4_per_request():
    r = _zhipu_by_model()["CogView-4"][0]
    assert r.input_per_1m == 0.06 and r.billing_unit == "request"


def test_multimodal_cogvideox2():
    r = _zhipu_by_model()["CogVideoX-2"][0]
    assert r.input_per_1m == 0.5 and r.billing_unit == "request"


def test_multimodal_cogvideox3():
    r = _zhipu_by_model()["CogVideoX-3"][0]
    assert r.input_per_1m == 1.0 and r.billing_unit == "request"


# ===================================================================
#  6. 推理区 — Real-time（10K Chats / per request / minute）
# ===================================================================

def test_realtime_cogtts():
    r = _zhipu_by_model()["CogTTS"][0]
    assert r.input_per_1m == 4.0 and r.billing_unit == "10k_chats"


def test_realtime_cogtts_clone():
    r = _zhipu_by_model()["CogTTS-Clone"][0]
    assert r.input_per_1m == 6.0 and r.billing_unit == "request"


def test_realtime_glm4_voice():
    r = _zhipu_by_model()["GLM-4-Voice"][0]
    assert r.input_per_1m == 80.0 and r.billing_unit == "token"


def test_realtime_glm_asr():
    r = _zhipu_by_model()["GLM-ASR"][0]
    assert r.input_per_1m == 0.06 and r.billing_unit == "minute"


# ===================================================================
#  7. 推理区 — Embedding Models
# ===================================================================

def test_embedding_3():
    r = _zhipu_by_model()["Embedding-3"][0]
    assert r.input_per_1m == 0.5 and r.output_per_1m == 0.5


def test_embedding_2():
    assert _zhipu_by_model()["Embedding-2"][0].input_per_1m == 0.5


# ===================================================================
#  8. 推理区 — More
# ===================================================================

def test_more_charglm4():
    assert _zhipu_by_model()["CharGLM-4"][0].input_per_1m == 1.0


def test_more_emohaa():
    assert _zhipu_by_model()["Emohaa"][0].input_per_1m == 15.0


def test_more_codegeex4():
    assert _zhipu_by_model()["CodeGeeX-4"][0].input_per_1m == 0.1


def test_more_rerank():
    assert _zhipu_by_model()["Rerank"][0].input_per_1m == 0.8


# ===================================================================
#  9. 搜索工具服务（¥X / time → billing_unit=request）
# ===================================================================

def test_search_std():
    r = _zhipu_by_model()["Search-Std"][0]
    assert r.input_per_1m == 0.01 and r.billing_unit == "request"
    assert r.modality == "search"


def test_search_pro():
    assert _zhipu_by_model()["Search-Pro"][0].input_per_1m == 0.03


def test_search_pro_sogou():
    assert _zhipu_by_model()["Search-Pro-Sogou"][0].input_per_1m == 0.05


def test_search_pro_quark():
    assert _zhipu_by_model()["Search-Pro-Quark"][0].input_per_1m == 0.05


# ===================================================================
# 10. 知识库扩容（¥X / GB / hour）
# ===================================================================

def test_knowledge_capacity():
    r = _zhipu_by_model()["knowledge_capacity"][0]
    assert r.input_per_1m == 0.04 and r.billing_unit == "gb_hour"
    assert r.modality == "storage"


# ===================================================================
# 11. 微调 Training（LoRA / Full，含 Not Supported 跳过）
# ===================================================================

def test_finetune_training_glm45_lora_and_full():
    rows = [r for r in _zhipu_by_model()["GLM-4.5"] if r.modality == "training"]
    by_svc = {r.service_tier: r for r in rows}
    assert by_svc["lora"].input_per_1m == 100.0   # 0.1 * 1000
    assert by_svc["full"].input_per_1m == 125.0    # 0.125 * 1000
    assert by_svc["lora"].deployment_version == "32k"
    assert by_svc["full"].deployment_version == "16k"


def test_finetune_training_glm45_air_both():
    rows = [r for r in _zhipu_by_model()["GLM-4.5-Air"] if r.modality == "training"]
    by_svc = {r.service_tier: r for r in rows}
    assert by_svc["lora"].input_per_1m == 35.0     # 0.035 * 1000
    assert by_svc["full"].input_per_1m == 50.0      # 0.05 * 1000


def test_finetune_training_chatglm3_lora_only():
    rows = [r for r in _zhipu_by_model()["ChatGLM3-6B"] if r.modality == "training"]
    assert len(rows) == 1 and rows[0].service_tier == "lora"
    assert rows[0].input_per_1m == 25.0


def test_finetune_training_cogview3_full_only():
    rows = [r for r in _zhipu_by_model()["Cogview-3"] if r.modality == "training"]
    assert len(rows) == 1 and rows[0].service_tier == "full"
    assert rows[0].input_per_1m == 20.0


def test_finetune_training_glm4v_lora_only():
    rows = [r for r in _zhipu_by_model()["GLM-4V"] if r.modality == "training"]
    assert len(rows) == 1 and rows[0].service_tier == "lora"
    assert rows[0].input_per_1m == 30.0


# ===================================================================
# 12. 微调 Inference（Public Instance，排除 GPU Unit / Day）
# ===================================================================

def test_finetune_inference_glm4_air():
    rows = [r for r in _zhipu_by_model()["GLM-4-Air"] if r.modality == "finetune-inference"]
    assert len(rows) == 1
    assert rows[0].input_per_1m == 3.0     # 0.003 * 1000


def test_finetune_inference_glm4_flash():
    rows = [r for r in _zhipu_by_model()["GLM-4-Flash"] if r.modality == "finetune-inference"]
    assert len(rows) == 1
    assert rows[0].input_per_1m == 2.0     # 0.002 * 1000


def test_finetune_inference_glm4_0520():
    rows = [r for r in _zhipu_by_model()["GLM-4-0520"] if r.modality == "finetune-inference"]
    assert len(rows) == 1
    assert rows[0].input_per_1m == 500.0   # 0.5 * 1000


def test_finetune_inference_skips_gpu_private():
    """微调推理只取 Public Instance 价，不含 Cogview-3 / GLM-4V（仅 Private Instance）。"""
    rows = [r for r in _zhipu_parse() if r.modality == "finetune-inference"]
    models = {r.model for r in rows}
    assert "Cogview-3" not in models   # Not Supported in Public Instance
    assert "GLM-4V" not in models      # Not Supported in Public Instance


# ===================================================================
# 13. 全局一致性
# ===================================================================

def test_zhipu_all_official_channel():
    for row in _zhipu_parse():
        assert row.channel == "official"
        assert row.provider == "zhipu"
        assert row.currency == Currency.CNY
        assert row.region == Region.CN


def test_zhipu_total_count():
    """76 条记录，覆盖 50 个唯一模型/工具。"""
    rows = _zhipu_parse()
    models = {r.model for r in rows}
    assert len(rows) >= 76
    assert len(models) >= 50


def test_zhipu_modality_counts():
    by_mod = _zhipu_by_modality()
    assert len(by_mod.get("text", [])) >= 40
    assert len(by_mod.get("multimodal", [])) >= 9
    assert len(by_mod.get("search", [])) == 4
    assert len(by_mod.get("storage", [])) == 1
    assert len(by_mod.get("training", [])) >= 15
    assert len(by_mod.get("finetune-inference", [])) >= 5


def test_no_private_instance_gpu():
    """确保没有任何 GPU Unit / Day 的私有实例价格泄入结果。"""
    for row in _zhipu_parse():
        assert row.billing_unit != "gpu_day"


# ===================================================================
#  MiniMax（保留原有测试）
# ===================================================================

# ===================================================================
#  MiniMax
# ===================================================================

def _minimax_parse():
    return MiniMaxScraper().parse(read_fixture("minimax_body.txt"))


def _minimax_by_model():
    by: dict[str, list] = {}
    for r in _minimax_parse():
        by.setdefault(r.model, []).append(r)
    return by


def test_minimax_language_m3_tiers():
    m3 = {r.context_range: r for r in _minimax_by_model()["MiniMax-M3"]}
    assert m3["0-512k"].input_per_1m == 2.10 and m3["0-512k"].output_per_1m == 8.40
    assert m3[">512k"].input_per_1m == 4.20
    assert m3["0-512k"].cached_input_per_1m == 0.42


def test_minimax_language_m27():
    r = _minimax_by_model()["MiniMax-M2.7"][0]
    assert r.context_range is None
    assert r.input_per_1m == 2.1


def test_minimax_tts_speech():
    by = _minimax_by_model()
    assert by["speech-2.8-hd"][0].input_per_1m == 3.5
    assert by["speech-2.8-hd"][0].billing_unit == "10k_chars"
    assert by["speech-2.8-turbo"][0].input_per_1m == 2.0


def test_minimax_voice_design():
    by = _minimax_by_model()
    assert by["voice-design"][0].input_per_1m == 9.9
    assert by["voice-cloning"][0].input_per_1m == 9.9


def test_minimax_video_h3():
    by = _minimax_by_model()
    assert by["MiniMax-H3-2k"][0].input_per_1m == 0.8
    assert by["MiniMax-H3-2k"][0].billing_unit == "second"
    assert by["MiniMax-H3-768p"][0].input_per_1m == 0.5


def test_minimax_video_context_ir():
    r = _minimax_by_model()["MiniMax-H3-Context-IR"][0]
    assert r.input_per_1m == 5.8 and r.output_per_1m == 23.0


def test_minimax_image():
    by = _minimax_by_model()
    assert by["image-01"][0].input_per_1m == 0.025
    assert by["image-01-live"][0].billing_unit == "per_image"


def test_minimax_mcp_vlm():
    r = _minimax_by_model()["API-vlm"][0]
    assert r.input_per_1m == 0.025 and r.billing_unit == "request"


def test_minimax_web_search():
    r = _minimax_by_model()["web_search"][0]
    assert r.input_per_1m == 0.03 and r.billing_unit == "request"


def test_minimax_total_count():
    rows = _minimax_parse()
    assert len(rows) >= 16


def test_official_channel_flag():
    for row in ZhipuScraper().parse(read_fixture("zhipu_body.txt")):
        assert row.channel == "official"
    for row in MiniMaxScraper().parse(read_fixture("minimax_body.txt")):
        assert row.channel == "official"
