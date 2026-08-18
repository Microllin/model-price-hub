"""轻量 Discovery 服务：RSS、官方网页和可选 SearXNG 搜索。"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from app.config import settings
from app.pipeline.store import load_latest_snapshot, write_snapshot
from app.db.session import sync_entries
from app.models.canonical import canonicalize

ROOT = Path(__file__).resolve().parent
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, provider TEXT, name TEXT, type TEXT, url TEXT, source_tier INTEGER DEFAULT 3, enabled INTEGER DEFAULT 1, last_success_at TEXT, last_error TEXT, consecutive_failures INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, provider TEXT NOT NULL, title TEXT NOT NULL, summary TEXT DEFAULT '', url TEXT NOT NULL, canonical_url TEXT NOT NULL, content_hash TEXT NOT NULL, published_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, seen_count INTEGER DEFAULT 1, UNIQUE(source_id, canonical_url));
CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, model TEXT, canonical_model TEXT, change_type TEXT DEFAULT 'news', status TEXT DEFAULT 'candidate', confidence TEXT DEFAULT 'low', source_tier INTEGER DEFAULT 3, title TEXT, summary TEXT, source_url TEXT, source_domain TEXT, published_at TEXT, detected_at TEXT, keywords TEXT, evidence TEXT, proposed_change TEXT, reviewed_at TEXT, review_note TEXT);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT, fetched INTEGER DEFAULT 0, new_items INTEGER DEFAULT 0, candidates INTEGER DEFAULT 0, error TEXT);
"""

KEYWORDS = {
    "model_added": ["new model", "introducing", "launch", "now available", "上线", "发布", "新模型", "可用"],
    "model_removed": ["deprecated", "deprecation", "sunset", "retired", "shutdown", "下线", "弃用", "停止服务"],
    "price_changed": ["pricing", "price", "cost", "价格", "计费", "降价", "涨价", "价格调整"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.discovery_db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 增量兼容已有 Discovery 数据库。
    for table, column, definition in (("sources", "source_tier", "INTEGER DEFAULT 3"), ("candidates", "source_tier", "INTEGER DEFAULT 3")):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return conn


def _config() -> dict:
    config = yaml.safe_load((ROOT / "providers.yaml").read_text(encoding="utf-8")) or {}
    if settings.searxng_url:
        config.setdefault("searxng", {})["base_url"] = settings.searxng_url
    return config


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _classify(title: str, summary: str, url: str = "") -> tuple[str, str, list[str], bool]:
    """严格召回：标题必须表达模型变更，通用文档/FAQ/价格表不生成候选。"""
    title_text = title.lower().strip()
    text = f"{title} {summary}".lower()
    hits = [word for words in KEYWORDS.values() for word in words if word.lower() in text]
    model_hint = bool(re.search(r"\b(?:gpt|grok|claude|gemini|deepseek|glm|qwen|kimi)(?:[\s._-]*[\w.-]+)?\b", text + " " + url, re.I)) or "模型" in text
    if not model_hint:
        return "news", "low", hits, False

    # 官方模型详情页是高可信上线证据；仅限明确的单模型页，不包含总目录。
    official_model_page = bool(re.search(r"/(?:models?)/(?:[^/]+/)*(?:grok|gpt|claude|gemini|deepseek|glm|qwen|kimi)[\w.-]*(?:[/#]|$)", url, re.I) or re.search(r"/guide/models/(?:[^/]+/)*(?:grok|gpt|claude|gemini|deepseek|glm|qwen|kimi)[\w.-]*(?:[/#]|$)", url, re.I)) and bool(re.search(r"\b(?:gpt|grok|claude|gemini|deepseek|glm|qwen|kimi)(?:[\s._-]*[\w.-]+)?\b", text + " " + url, re.I))
    if official_model_page and not any(x in title_text for x in ("pricing", "deprecation", "retirement", "legacy", "faq")):
        return "model_added", "high", hits + ["官方单模型页面"], True

    # 明确排除：这些页面通常只是 API 文档、FAQ、套餐/活动、错误码或通用价格目录。
    noise = ("faq", "error code", "错误码", "comparison", "对比", "活动规则", "subscription agreement", "pricing -", "pricing |", "模型调用价格", "models & pricing", "model deprecations")
    if any(x in title_text for x in noise):
        # 只有标题本身明确写出“模型下线/弃用”才允许通过。
        if not re.search(r"(model|模型).*(retir|deprecat|sunset|下线|弃用|停止)", title_text, re.I):
            return "news", "low", hits, False

    # 上下线：标题必须出现明确动作，不能只因正文带有 deprecated/available。
    removed = re.search(r"(model|模型|grok|gpt|claude|gemini|deepseek|glm|qwen|kimi).*(retir|deprecat|sunset|shut.?down|下线|弃用|停止服务)", title_text, re.I)
    added = re.search(r"(introduc|launch|release|new model|now available|上线|发布|新模型).*(gpt|grok|claude|gemini|deepseek|glm|qwen|kimi|模型)", title_text, re.I)
    if removed:
        return "model_removed", "high", hits, False
    if added:
        return "model_added", "high", hits, True

    # 价格：必须是“模型 + 明确调整动作”，单纯价格表/价格详情页面不算变更。
    price_change = re.search(r"(price|pricing|cost|价格|计费).*(change|update|reduc|decreas|increas|调整|变更|降价|涨价|更新)", text, re.I)
    model_release_with_price = re.search(r"(release|launch|introduc|发布|上线).*(pricing|price|价格|计费)", text, re.I)
    title_price_change = re.search(r"(pricing|price).*(update|change|reduc|decreas|increas|调整|变更|降价|涨价)", title_text, re.I)
    if (price_change or model_release_with_price or title_price_change) and model_hint:
        return "price_changed", "high" if title_price_change or model_release_with_price else "medium", hits, False
    return "news", "low", hits, False


def _model(title: str, url: str = "") -> str | None:
    # 优先从官方模型 URL 提取标准 ID，再回退标题。
    match = re.search(r"/(?:models?)/(?:[^/]+/)*(?:gpt|grok|claude|gemini|deepseek|glm|qwen|kimi)[\w.-]*(?:[/#]|$)", url, re.I) or re.search(r"/guide/models/(?:[^/]+/)*(?:gpt|grok|claude|gemini|deepseek|glm|qwen|kimi)[\w.-]*(?:[/#]|$)", url, re.I)
    if match:
        return match.group(0).rstrip('/#').split('/')[-1].split('#', 1)[0]
    match = re.search(r"\b((?:gpt|grok|claude|gemini|deepseek|glm|qwen|kimi)[\s._-]*[\w.-]{1,})\b", title, re.I)
    return match.group(1) if match else None


def _allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _insert(conn: sqlite3.Connection, source_id: str, provider: str, title: str, summary: str, url: str, published: str | None, domains: list[str], source_tier: int = 3) -> tuple[int, int]:
    if not _allowed(url, domains):
        return 0, 0
    # 某些文档站的导航锚点标题是“Skip to main content”，用模型 URL 补足可读标题。
    url_model = _model(title, url)
    if url_model and title.lower().strip() in {"skip to main content", "", "home", "首页"}:
        title = f"{url_model} 官方模型页面"
    now = _now(); canonical = url.split("#", 1)[0].rstrip("/"); item_id = _hash(canonical)
    # 同一官方页面可能被多个查询命中：全局按规范 URL 去重，而不是按搜索词去重。
    old = conn.execute("SELECT id FROM items WHERE canonical_url=?", (canonical,)).fetchone()
    if old:
        conn.execute("UPDATE items SET last_seen_at=?, seen_count=seen_count+1 WHERE id=?", (now, item_id))
        # 同一 URL 先由搜索发现、后由配置的官方页面命中时，必须提升证据层级。
        candidate = conn.execute("SELECT id,source_tier,change_type FROM candidates WHERE item_id=?", (old["id"],)).fetchone()
        kind, confidence, _, auto_apply = _classify(title, summary, url)
        if candidate and source_tier < candidate["source_tier"] and kind == "model_added":
            status = "verified" if source_tier == 1 and auto_apply else "candidate"
            evidence = json.dumps(["官方域名白名单", f"证据层级：{source_tier}", "官方单模型页面"], ensure_ascii=False)
            conn.execute("UPDATE candidates SET source_tier=?,status=?,confidence=?,evidence=?,detected_at=?,reviewed_at=?,review_note=? WHERE id=?", (source_tier, status, "high" if source_tier == 1 else confidence, evidence, now, now if status == "verified" else None, "官方直接页面提升证据层级" if status == "verified" else None, candidate["id"]))
        conn.commit(); return 0, 0
    conn.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (item_id, source_id, provider, title[:500], summary[:3000], url, canonical, _hash(title + summary), published, now, now, 1))
    kind, confidence, hits, auto_apply = _classify(title, summary, url)
    if source_tier == 1 and kind in {"model_added", "model_removed"}:
        confidence, auto_apply = "high", True
    elif source_tier != 1:
        # 价格页只作为第二层价格/存在性证据，不自动执行模型上线。
        auto_apply = False
    if kind == "news":
        conn.commit(); return 1, 0
    candidate_id = _hash("candidate|" + item_id)
    status = "verified" if auto_apply and kind in {"model_added", "model_removed"} else "candidate"
    review_note = "第一层官方页面自动验证" if status == "verified" else None
    conn.execute("INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (candidate_id, item_id, provider, _model(title, url), None, kind, status, confidence, source_tier, title[:500], summary[:3000] or title, url, urlparse(url).hostname or "", published, now, json.dumps(hits, ensure_ascii=False), json.dumps(["官方域名白名单", f"证据层级：{source_tier}"] + (["官方单模型页面"] if auto_apply else []), ensure_ascii=False), "{}", now if status == "verified" else None, review_note))
    conn.commit(); return 1, 1


def _rss_items(source: dict, provider: dict) -> list[tuple[str, str, str | None]]:
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": settings.user_agent}, **({"proxy": settings.http_proxy} if settings.http_proxy else {})) as client:
        response = client.get(source["url"])
        response.raise_for_status()
        text = response.text
    root = ET.fromstring(text)
    result = []
    for entry in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = entry.findtext("title") or ""
        link = entry.findtext("link") or ""
        if not link:
            node = entry.find("{http://www.w3.org/2005/Atom}link"); link = node.attrib.get("href", "") if node is not None else ""
        summary = entry.findtext("description") or entry.findtext("{http://www.w3.org/2005/Atom}summary") or ""
        result.append((html.unescape(re.sub("<[^>]+>", "", title)).strip(), html.unescape(re.sub("<[^>]+>", "", summary)).strip(), link))
    return result


def _html_items(source: dict) -> list[tuple[str, str, str | None]]:
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": settings.user_agent}, **({"proxy": settings.http_proxy} if settings.http_proxy else {})) as client:
        response = client.get(source["url"])
        response.raise_for_status()
        text = response.text
    clean = lambda value: html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()
    results = []
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.I | re.S):
        href, anchor = match.group(1), clean(match.group(2))
        url = urljoin(source["url"], href)
        if anchor and url != source["url"] and any(token in (anchor + url).lower() for token in ("grok", "model", "release", "pricing", "news", "上线", "价格")):
            results.append((anchor[:500], anchor[:3000], url))
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    description = re.search(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\'](.*?)["\']', text, re.I | re.S)
    direct = (clean(title.group(1) if title else source.get("name", "官方公告")), clean(description.group(1) if description else "官方来源页面更新，请打开原文查看详情。"), source["url"])
    # 配置的直接页面必须作为自身证据入库；链接列表只是补充，不能取代它。
    return [direct] + results[:79]


def _collect_source(source: dict, provider: dict) -> list[tuple[str, str, str | None]]:
    try:
        return _rss_items(source, provider)
    except (ET.ParseError, ValueError):
        return _html_items(source)


def apply_verified_additions(since: str) -> dict:
    """仅发布本轮新验证/升级的第一层官方模型，避免历史候选集中补发。"""
    conn = _db()
    rows = conn.execute("SELECT * FROM candidates WHERE status='verified' AND change_type='model_added' AND source_tier=1 AND detected_at>=? AND model IS NOT NULL", (since,)).fetchall()
    now = _now()
    events = [{"event": "model_added", "occurred_at": now, "data_date": now[:10], "model": {"provider": r["provider"], "model": r["model"], "canonical_model": canonicalize(r["model"]), "channel": "official", "region": "intl", "currency": "USD", "service_tier": "standard", "modality": "text", "billing_unit": "token", "source": "official-page", "source_url": r["source_url"]}} for r in rows]
    conn.executemany("UPDATE candidates SET status='applied',reviewed_at=?,review_note=? WHERE id=?", [(now, "第一层官方模型上线已发布", r["id"]) for r in rows])
    conn.commit(); conn.close()
    return {"applied_models": len(rows), "events": events}


def apply_verified_removals() -> dict:
    """第一层官方下线直接生效：删除全部渠道价格，每个模型只产生一个官方事件。"""
    conn = _db()
    rows = conn.execute("SELECT * FROM candidates WHERE status='verified' AND change_type='model_removed' AND source_tier=1 AND model IS NOT NULL").fetchall()
    if not rows:
        conn.close(); return {"removed": 0, "applied_models": 0, "events": []}
    snap = load_latest_snapshot()
    targets = {(r["provider"], canonicalize(r["model"])) for r in rows}
    removed = [e for e in snap.entries if (e.provider, e.canonical_model) in targets] if snap else []
    if snap and removed:
        remaining = [e for e in snap.entries if (e.provider, e.canonical_model) not in targets]
        write_snapshot(remaining, snap.data_date)
        sync_entries(remaining)
    now = _now()
    data_date = snap.data_date if snap else now[:10]
    events = [{
        "event": "model_removed", "occurred_at": now, "data_date": data_date,
        "confirmations": 1,
        "model": {
            "provider": r["provider"], "model": r["model"],
            "canonical_model": canonicalize(r["model"]), "channel": "official",
            "region": "intl", "currency": "USD", "service_tier": "standard",
            "modality": "text", "billing_unit": "token", "source": "official-page",
            "source_url": r["source_url"],
        },
    } for r in rows]
    conn.executemany(
        "UPDATE candidates SET status='applied', reviewed_at=?, review_note=? WHERE id=?",
        [(now, "第一层官方下线页面自动执行", r["id"]) for r in rows],
    )
    conn.commit(); conn.close()
    return {"removed": len(removed), "applied_models": len(rows), "events": events}


def run() -> dict:
    started = _now(); conn = _db(); fetched = new_items = candidates = 0
    config = _config(); providers = {p["id"]: p for p in config.get("providers", [])}
    try:
        for provider in config.get("providers", []):
            for feed in provider.get("feeds", []):
                source_id = f"rss:{provider['id']}:{feed['id']}"
                source_tier = int(feed.get("source_tier", 1 if "models/" in feed["url"] or "/guide/models/" in feed["url"] else 2))
                conn.execute("INSERT OR IGNORE INTO sources(id,provider,name,type,url,source_tier) VALUES(?,?,?,?,?,?)", (source_id, provider["id"], feed.get("name", feed["id"]), "rss", feed["url"], source_tier))
                try:
                    for title, summary, url in _collect_source(feed, provider):
                        if url: fetched += 1; n, c = _insert(conn, source_id, provider["id"], title, summary, url, None, provider.get("domains", []), source_tier); new_items += n; candidates += c
                    conn.execute("UPDATE sources SET last_success_at=?, last_error=NULL, consecutive_failures=0 WHERE id=?", (_now(), source_id))
                except Exception as exc:
                    conn.execute("UPDATE sources SET last_error=?, consecutive_failures=consecutive_failures+1 WHERE id=?", (repr(exc), source_id))
            # SearXNG is optional and deliberately only stores official-domain results.
            sx = config.get("searxng", {})
            if sx.get("enabled"):
                for query in provider.get("queries", []):
                    source_id = f"searxng:{provider['id']}:{_hash(query)[:10]}"
                    try:
                        with httpx.Client(timeout=sx.get("timeout", 12), **({"proxy": settings.http_proxy} if settings.http_proxy else {})) as client:
                            data = client.get(sx["base_url"].rstrip("/") + "/search", params={"q": query, "format": "json"}).json()
                        configured_tiers = {
                            f["url"].split("#", 1)[0].rstrip("/"): int(f.get("source_tier", 3))
                            for f in provider.get("feeds", [])
                        }
                        for result in data.get("results", []):
                            result_url = result.get("url", "")
                            tier = configured_tiers.get(result_url.split("#", 1)[0].rstrip("/"), 3)
                            fetched += 1; n, c = _insert(conn, source_id, provider["id"], result.get("title", ""), result.get("content", ""), result_url, result.get("publishedDate"), provider.get("domains", []), tier); new_items += n; candidates += c
                    except Exception:
                        continue
        run_id = conn.execute("INSERT INTO runs(started_at,finished_at,status,fetched,new_items,candidates) VALUES(?,?,?,?,?,?)", (started, _now(), "success", fetched, new_items, candidates)).lastrowid
        conn.commit(); return {"run_id": run_id, "status": "success", "started_at": started, "fetched": fetched, "new_items": new_items, "candidates": candidates}
    except Exception as exc:
        conn.execute("INSERT INTO runs(started_at,finished_at,status,error) VALUES(?,?,?,?)", (started, _now(), "failed", repr(exc))); conn.commit(); raise
    finally:
        conn.close()
