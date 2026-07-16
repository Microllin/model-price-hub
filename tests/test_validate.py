"""校验/变更检测 + 冻结逻辑测试。"""
from __future__ import annotations

from app.models.pricing import Currency, PriceEntry, Provenance, RawPrice, Region
from app.pipeline.validate import apply_overrides, validate_and_merge


def _raw(model, inp, outp, provider="deepseek"):
    return RawPrice(
        provider=provider, channel="official", model=model,
        region=Region.CN, currency=Currency.CNY,
        input_per_1m=inp, output_per_1m=outp,
    )


def _entry(model, inp, outp, provider="deepseek", prov=Provenance.SCRAPED):
    return PriceEntry(
        provider=provider, channel="official", model=model,
        region=Region.CN, currency=Currency.CNY,
        input_per_1m=inp, output_per_1m=outp, provenance=prov,
    )


def test_new_model_added():
    entries, report = validate_and_merge([_raw("m1", 1.0, 2.0)], [])
    assert len(entries) == 1
    assert len(report.added) == 1


def test_insane_price_dropped():
    entries, report = validate_and_merge([_raw("m1", -5.0, 2.0)], [])
    assert entries == []
    assert len(report.dropped) == 1


def test_large_change_is_frozen_and_keeps_old_value():
    prev = [_entry("m1", 1.0, 2.0)]
    # 输入价从 1 → 10(+900%),超过默认 40% 阈值 → 冻结
    entries, report = validate_and_merge([_raw("m1", 10.0, 2.0)], prev)
    assert len(report.frozen) == 1
    frozen = entries[0]
    assert frozen.input_per_1m == 1.0            # 保留旧值
    assert frozen.provenance == Provenance.STALE


def test_small_change_accepted():
    prev = [_entry("m1", 1.0, 2.0)]
    entries, report = validate_and_merge([_raw("m1", 1.1, 2.0)], prev)  # +10%
    assert entries[0].input_per_1m == 1.1
    assert len(report.changed) == 1


def test_disappeared_scraped_entry_marked_stale():
    prev = [_entry("gone", 1.0, 2.0)]
    entries, report = validate_and_merge([_raw("m1", 1.0, 2.0)], prev)
    labels = {e.model: e for e in entries}
    assert labels["gone"].provenance == Provenance.STALE
    assert len(report.removed) == 1


def test_override_takes_precedence():
    scraped = [_entry("m1", 1.0, 2.0)]
    override = [_entry("m1", 9.9, 9.9, prov=Provenance.MANUAL)]
    merged = apply_overrides(scraped, override)
    assert len(merged) == 1
    assert merged[0].input_per_1m == 9.9
    assert merged[0].provenance == Provenance.MANUAL
