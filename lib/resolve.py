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


RETRY_CODES = ("blocked", "quota")
RETRY_BACKOFF = (20, 60)


def resolve_with_retry(url: str, cookie: str | None = None, attempts: int = 3) -> dict:
    """解析并对外站点抖动（502/配额页）做退避重试。

    91porn 在批量采集时会对同一出口 IP 短时返回 502，页面本身没坏；
    等 20s/60s 后换一次 visitor cookie 通常就能恢复，因此这里重试而不是直接判失败。
    """
    import time

    last: Exception | None = None
    for i in range(attempts):
        try:
            return resolve(url, cookie=cookie)
        except registry.ResolveError as exc:
            last = exc
            if exc.code not in RETRY_CODES or i == attempts - 1:
                raise
            time.sleep(RETRY_BACKOFF[min(i, len(RETRY_BACKOFF) - 1)])
    raise last  # type: ignore[misc]
