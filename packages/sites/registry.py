"""站点注册表：URL → extractor，external_key → 入口 URL。

唯一真源在这里；主仓不再保留解析代码（plan 20261103 §5 / AGENTS.md「解析与上传通道」）。
新增站点 = 本目录一个模块（SITE / can_handle / entry_from_key / resolve_sync）+ 下表一行。
"""
from __future__ import annotations

import importlib

from . import common

# 模块名 91porn 以数字开头，必须用 importlib 而非直接 import 语句。
_91PORN = importlib.import_module("packages.sites.91porn")
_DOUYIN = importlib.import_module("packages.sites.douyin")
_XHS = importlib.import_module("packages.sites.xiaohongshu")
_HAIJIAO = importlib.import_module("packages.sites.haijiao")

SITES = {
    "91porn": _91PORN,
    "douyin": _DOUYIN,
    "xiaohongshu": _XHS,
    "haijiao": _HAIJIAO,
}

__all__ = ["SITES", "match_url", "entry_from_key", "site_of_key", "resolve_url", "ResolveError"]

ResolveError = common.ResolveError


def match_url(url: str):
    """返回能处理该 URL 的站点模块；无人认领返回 None。"""
    for module in SITES.values():
        try:
            if module.can_handle(url):
                return module
        except Exception:  # 单个站点判定异常不应拖垮整体匹配
            continue
    return None


def site_of_key(external_key: str) -> str | None:
    """`91porn:viewkey=abc` → `91porn`。"""
    if not external_key or ":" not in external_key:
        return None
    prefix = external_key.split(":", 1)[0]
    return prefix if prefix in SITES else None


def entry_from_key(external_key: str) -> str | None:
    """去重键 → 可解析的入口 URL（采集编排用它把 key 还原成任务）。"""
    site = site_of_key(external_key)
    if not site:
        return None
    try:
        return SITES[site].entry_from_key(external_key)
    except Exception:
        return None


def resolve_url(page_url: str, cookie: str | None = None) -> dict:
    """同步解析一个页面链接，产出 common.py 约定的统一形状。"""
    module = match_url(page_url)
    if module is None:
        raise common.ResolveError("unsupported-form", f"no extractor for {page_url}")
    return module.resolve_sync(page_url, cookie=cookie)
