"""schema_v2 数据查看 API + 内嵌前端。

启动:
    python -m app.serve_v2

提供:
    GET /                  → 数据浏览前端页
    GET /api/stats         → 统计概览
    GET /api/providers     → 厂商列表
    GET /api/models        → 模型列表（支持 ?provider= 过滤）
    GET /api/prices        → 价格列表（支持多维过滤 + 分页）
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, func, distinct
from sqlalchemy.orm import Session, sessionmaker

from app.models.schema_v2 import (
    Base, Provider, Model, PricePlan, ScrapeRun,
    ProviderType, BillingUnit,
)

DB_PATH = Path("data/v2.sqlite")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

app = FastAPI(title="Price Hub v2 — 数据浏览器")


# ─── API ─────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    with SessionLocal() as s:
        return {
            "providers": s.query(func.count(Provider.id)).scalar(),
            "models": s.query(func.count(Model.id)).scalar(),
            "price_plans": s.query(func.count(PricePlan.id)).scalar(),
            "official_plans": s.query(func.count(PricePlan.id)).filter(PricePlan.channel == "official").scalar(),
            "channels": s.query(func.count(distinct(PricePlan.channel))).scalar(),
            "billing_units": [r[0] for r in s.query(distinct(PricePlan.billing_unit)).all()],
            "modalities": [r[0] for r in s.query(distinct(Model.modality)).all()],
        }


@app.get("/api/providers")
def list_providers():
    with SessionLocal() as s:
        rows = s.query(
            Provider.slug, Provider.display_name, Provider.type,
            func.count(Model.id).label("model_count"),
        ).outerjoin(Model).group_by(Provider.id).order_by(Provider.slug).all()
        return [
            {"slug": r.slug, "display_name": r.display_name,
             "type": r.type.value if isinstance(r.type, ProviderType) else r.type,
             "model_count": r.model_count}
            for r in rows
        ]


@app.get("/api/models")
def list_models(
    provider: str | None = None,
    modality: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    with SessionLocal() as s:
        query = s.query(
            Model.id, Model.model_id, Model.canonical, Model.modality,
            Model.context_window, Model.max_output,
            Provider.slug.label("provider"),
        ).join(Provider)
        if provider:
            query = query.filter(Provider.slug == provider)
        if modality:
            query = query.filter(Model.modality == modality)
        if q:
            query = query.filter(Model.model_id.ilike(f"%{q}%"))
        total = query.count()
        rows = query.order_by(Provider.slug, Model.model_id).offset(offset).limit(limit).all()
        return {
            "total": total,
            "items": [
                {"id": r.id, "provider": r.provider, "model_id": r.model_id,
                 "canonical": r.canonical, "modality": r.modality,
                 "context_window": r.context_window, "max_output": r.max_output}
                for r in rows
            ],
        }


@app.get("/api/prices")
def list_prices(
    provider: str | None = None,
    model: str | None = None,
    channel: str | None = None,
    currency: str | None = None,
    billing_unit: str | None = None,
    service_tier: str | None = None,
    official: bool | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    with SessionLocal() as s:
        query = s.query(
            PricePlan, Model.model_id, Model.modality,
            Model.context_window, Provider.slug.label("provider_slug"),
            Provider.display_name.label("provider_name"),
        ).select_from(PricePlan).join(Model, PricePlan.model_id == Model.id).join(Provider, Model.provider_id == Provider.id)

        if provider:
            query = query.filter(Provider.slug == provider)
        if model:
            query = query.filter(Model.model_id.ilike(f"%{model}%"))
        if channel:
            query = query.filter(PricePlan.channel == channel)
        if currency:
            query = query.filter(PricePlan.currency == currency)
        if billing_unit:
            query = query.filter(PricePlan.billing_unit == billing_unit)
        if service_tier:
            query = query.filter(PricePlan.service_tier == service_tier)
        if official is True:
            query = query.filter(PricePlan.channel == "official")
        elif official is False:
            query = query.filter(PricePlan.channel != "official")
        if q:
            query = query.filter(Model.model_id.ilike(f"%{q}%"))

        total = query.count()
        rows = query.order_by(
            Provider.slug, Model.model_id,
            PricePlan.service_tier, PricePlan.context_range,
        ).offset(offset).limit(limit).all()

        return {
            "total": total,
            "items": [
                {
                    "provider": r.provider_slug,
                    "provider_name": r.provider_name,
                    "model": r.model_id,
                    "modality": r.modality,
                    "channel": r[0].channel,
                    "currency": r[0].currency.value if hasattr(r[0].currency, 'value') else r[0].currency,
                    "region": r[0].region.value if hasattr(r[0].region, 'value') else r[0].region,
                    "billing_unit": r[0].billing_unit.value if hasattr(r[0].billing_unit, 'value') else r[0].billing_unit,
                    "service_tier": r[0].service_tier,
                    "context_range": r[0].context_range,
                    "context_window": r.context_window,
                    "input_price": float(r[0].input_price) if r[0].input_price is not None else None,
                    "output_price": float(r[0].output_price) if r[0].output_price is not None else None,
                    "cached_read": float(r[0].cached_read) if r[0].cached_read is not None else None,
                    "cached_write": float(r[0].cached_write) if r[0].cached_write is not None else None,
                    "source": r[0].source,
                    "time_window": r[0].time_window,
                }
                for r in rows
            ],
        }


# ─── 前端页面 ────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Price Hub v2 — 数据浏览器</title>
<style>
  :root { --bg: #0f172a; --card: #1e293b; --border: #334155; --text: #e2e8f0; --muted: #94a3b8; --accent: #818cf8; --green: #34d399; --red: #f87171; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Inter', -apple-system, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .header { background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); padding: 24px 32px; border-bottom: 1px solid var(--border); }
  .header h1 { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
  .header h1 span { font-size: 28px; }
  .stats { display: flex; gap: 24px; margin-top: 12px; flex-wrap: wrap; }
  .stat { background: rgba(255,255,255,0.06); padding: 8px 16px; border-radius: 8px; }
  .stat .n { font-size: 20px; font-weight: 700; color: var(--accent); }
  .stat .l { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .container { max-width: 1440px; margin: 0 auto; padding: 20px 24px; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
  .filters select, .filters input { background: var(--card); color: var(--text); border: 1px solid var(--border); padding: 6px 10px; border-radius: 6px; font-size: 13px; }
  .filters input { width: 200px; }
  .filters select { min-width: 120px; }
  .btn { background: var(--accent); color: #fff; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .btn:hover { opacity: 0.85; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead { position: sticky; top: 0; z-index: 1; }
  th { background: var(--card); color: var(--muted); text-align: left; padding: 8px 10px; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; border-bottom: 2px solid var(--border); white-space: nowrap; }
  td { padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  tr:hover td { background: rgba(129,140,248,0.06); }
  .price { font-variant-numeric: tabular-nums; font-weight: 600; }
  .price.in { color: var(--green); }
  .price.out { color: var(--accent); }
  .price.cache { color: var(--muted); }
  .tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 500; }
  .tag.official { background: rgba(52,211,153,0.15); color: var(--green); }
  .tag.third { background: rgba(148,163,184,0.15); color: var(--muted); }
  .tag.token { background: rgba(129,140,248,0.12); color: var(--accent); }
  .tag.request { background: rgba(251,146,60,0.15); color: #fb923c; }
  .tag.second { background: rgba(244,114,182,0.15); color: #f472b6; }
  .tag.other { background: rgba(148,163,184,0.1); color: var(--muted); }
  .pager { display: flex; gap: 8px; justify-content: center; margin-top: 16px; align-items: center; color: var(--muted); font-size: 13px; }
  .empty { text-align: center; padding: 40px; color: var(--muted); }
  .count { font-size: 13px; color: var(--muted); margin-bottom: 8px; }
</style>
</head>
<body>
<div class="header">
  <h1><span>📊</span> Price Hub v2 — 数据浏览器</h1>
  <div class="stats" id="stats"></div>
</div>
<div class="container">
  <div class="filters" id="filters"></div>
  <div class="count" id="count"></div>
  <div style="overflow-x:auto">
    <table><thead id="thead"></thead><tbody id="tbody"></tbody></table>
  </div>
  <div class="pager" id="pager"></div>
</div>
<script>
const API = '/api';
let state = { provider:'', channel:'', currency:'', billing_unit:'', q:'', official:'', offset:0, limit:100 };
let providers = [];

async function init() {
  const [st, prov] = await Promise.all([
    fetch(`${API}/stats`).then(r=>r.json()),
    fetch(`${API}/providers`).then(r=>r.json()),
  ]);
  providers = prov;
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="n">${st.providers}</div><div class="l">厂商</div></div>
    <div class="stat"><div class="n">${st.models}</div><div class="l">模型</div></div>
    <div class="stat"><div class="n">${st.price_plans}</div><div class="l">价格方案</div></div>
    <div class="stat"><div class="n">${st.official_plans}</div><div class="l">官方价格</div></div>
    <div class="stat"><div class="n">${st.channels}</div><div class="l">渠道</div></div>
  `;
  renderFilters();
  loadPrices();
}

function renderFilters() {
  const provOpts = providers.map(p => `<option value="${p.slug}">${p.display_name} (${p.model_count})</option>`).join('');
  document.getElementById('filters').innerHTML = `
    <select onchange="setFilter('provider',this.value)"><option value="">全部厂商</option>${provOpts}</select>
    <select onchange="setFilter('channel',this.value)"><option value="">全部渠道</option><option value="official">🏷️ 官方</option></select>
    <select onchange="setFilter('currency',this.value)"><option value="">全部货币</option><option value="CNY">¥ CNY</option><option value="USD">$ USD</option></select>
    <select onchange="setFilter('billing_unit',this.value)"><option value="">全部计费</option><option value="token">token</option><option value="request">request</option><option value="second">second</option><option value="minute">minute</option><option value="per_image">per_image</option><option value="10k_chars">10k_chars</option><option value="gb_hour">gb_hour</option></select>
    <input type="text" placeholder="搜索模型名..." oninput="setFilter('q',this.value)">
    <button class="btn" onclick="loadPrices()">刷新</button>
  `;
}

function setFilter(k,v) { state[k]=v; state.offset=0; loadPrices(); }

async function loadPrices() {
  const p = new URLSearchParams();
  for (const [k,v] of Object.entries(state)) if (v!=='') p.set(k,v);
  const data = await fetch(`${API}/prices?${p}`).then(r=>r.json());
  renderTable(data);
}

function fmtPrice(v, cls) {
  if (v===null||v===undefined) return '<td class="price">—</td>';
  return `<td class="price ${cls}">${v.toFixed(4)}</td>`;
}

function tagCls(ch) { return ch==='official'?'official':'third'; }
function buTag(bu) {
  const cls = bu==='token'?'token': bu==='request'?'request': bu==='second'?'second':'other';
  return `<span class="tag ${cls}">${bu}</span>`;
}

function renderTable(data) {
  document.getElementById('count').textContent = `共 ${data.total} 条，显示 ${data.items.length} 条`;
  document.getElementById('thead').innerHTML = `<tr>
    <th>厂商</th><th>模型</th><th>渠道</th><th>模态</th><th>计费</th>
    <th>服务档</th><th>上下文档</th><th>币种</th>
    <th>输入价</th><th>输出价</th><th>缓存读</th><th>缓存写</th><th>来源</th>
  </tr>`;
  if (!data.items.length) {
    document.getElementById('tbody').innerHTML = '<tr><td colspan="13" class="empty">无数据</td></tr>';
    document.getElementById('pager').innerHTML = '';
    return;
  }
  document.getElementById('tbody').innerHTML = data.items.map(r => `<tr>
    <td>${r.provider}</td>
    <td><b>${r.model}</b></td>
    <td><span class="tag ${tagCls(r.channel)}">${r.channel}</span></td>
    <td>${r.modality}</td>
    <td>${buTag(r.billing_unit)}</td>
    <td>${r.service_tier==='standard'?'':r.service_tier}</td>
    <td>${r.context_range||''}</td>
    <td>${r.currency}</td>
    ${fmtPrice(r.input_price,'in')}
    ${fmtPrice(r.output_price,'out')}
    ${fmtPrice(r.cached_read,'cache')}
    ${fmtPrice(r.cached_write,'cache')}
    <td style="font-size:11px;color:var(--muted)">${r.source||''}</td>
  </tr>`).join('');

  // pager
  const pages = Math.ceil(data.total / state.limit);
  const cur = Math.floor(state.offset / state.limit);
  let html = '';
  if (cur > 0) html += `<button class="btn" onclick="gotoPage(${cur-1})">← 上页</button>`;
  html += `<span>第 ${cur+1}/${pages} 页</span>`;
  if (cur < pages-1) html += `<button class="btn" onclick="gotoPage(${cur+1})">下页 →</button>`;
  document.getElementById('pager').innerHTML = html;
}

function gotoPage(p) { state.offset = p * state.limit; loadPrices(); }

init();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
