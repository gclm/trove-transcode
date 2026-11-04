"""解析原语：URL → 统一元数据（packages/sites 是唯一真源）。"""
from __future__ import annotations

import os

from packages.sites import registry

# 站点凭据：runner 侧 secret，缺省时该站点按匿名解析（91porn 匿名只有 720p）。
COOKIE_ENV = {
    "91porn": "PORN91_COOKIE",
    "douyin": "DOUYIN_COOKIE",
    "xiaohongshu": "XHS_COOKIE",
    "haijiao": "HAIJIAO_COOKIE",
}


def site_of(url: str) -> str | None:
    module = registry.match_url(url)
    return getattr(module, "SITE", None) if module else None


def cookie_for(site: str | None) -> str | None:
    if not site:
        return None
    key = COOKIE_ENV.get(site)
    return os.environ.get(key) if key else None


def resolve(url: str, cookie: str | None = None) -> dict:
    """解析一个链接；失败抛 ResolveError（code ∈ not-found/blocked/quota/unsupported-form）。"""
    module = registry.match_url(url)
    if module is None:
        raise registry.ResolveError("unsupported-form", f"no extractor for {url}")
    site = getattr(module, "SITE", "")
    return module.resolve_sync(url, cookie=cookie if cookie is not None else cookie_for(site))


def resolve_key(external_key: str) -> dict:
    """去重键 → 入口 URL → 元数据（采集编排按 key 回填任务用）。"""
    entry = registry.entry_from_key(external_key)
    if not entry:
        raise registry.ResolveError("unsupported-form", f"unknown key {external_key}")
    return resolve(entry)
