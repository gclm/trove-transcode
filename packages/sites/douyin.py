"""douyin extractor：yt-dlp 优先，失败回落自写 feed 解析（对齐点 ⑧）。

自写路径移植真源：主仓 server/resolvers/douyin.ts（只读参考）——
iPhone UA 下 aweme.snssdk.com mobile feed 端点免签名；PC web API 有签名墙。
"""

import asyncio
import json
import re
from urllib.parse import quote

import httpx

from .common import MOBILE_UA, ResolveError

SITE = "douyin"
AWEME_ID_RE = re.compile(r"/video/(\d{15,})")
FEED_API = "https://aweme.snssdk.com/aweme/v1/feed/?aweme_id={aweme_id}"
AGGREGATE_API = "https://api-play.amemv.com/aweme/v1/play/?video_id={uri}"


def extract_aweme_id(url: str) -> str | None:
    m = AWEME_ID_RE.search(url)
    return m.group(1) if m else None


def can_handle(url: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return host == "v.douyin.com" or host in ("www.douyin.com", "douyin.com") or host.endswith("iesdouyin.com")


def entry_from_key(external_key: str) -> str:
    vid = external_key.split("video_id=", 1)[1]
    return f"https://www.douyin.com/video/{vid}"


# ---------- 纯函数：feed JSON → 元数据（真源 douyin.ts parseFeed） ----------

def _pick_stream_url(aweme: dict) -> str:
    """aggregate 端点每次 re-302 到新 CDN 节点，寿命长于签名 CDN 直链（~98min），优先。"""
    uri = (aweme.get("video") or {}).get("play_addr", {}).get("uri", "")
    return AGGREGATE_API.format(uri=quote(uri))


def parse_feed(payload: dict, aweme_id: str, cookie: str | None = None) -> dict:
    aweme_list = payload.get("aweme_list") or []
    if not aweme_list:
        raise ResolveError("not-found", "视频不存在、已删除或被审核屏蔽")
    aweme = aweme_list[0]

    if aweme.get("images"):
        raise ResolveError("unsupported-form", "图集内容暂不支持保存")
    play_addr = (aweme.get("video") or {}).get("play_addr") or {}
    if not play_addr.get("uri"):
        raise ResolveError("unsupported-form", "直播或非常规内容暂不支持保存")

    duration_ms = aweme.get("video", {}).get("duration") or 0
    headers = {"User-Agent": MOBILE_UA}
    if cookie:
        headers["Cookie"] = cookie
    return {
        "external_key": f"{SITE}:video_id={aweme_id}",
        "title": str(aweme.get("desc") or f"抖音视频 {aweme_id}")[:300],
        "duration_s": round(duration_ms / 1000) if duration_ms > 0 else None,
        "poster_url": ((aweme.get("video") or {}).get("cover") or {}).get("url_list", [None])[0],
        "download_url": _pick_stream_url(aweme),
        "download_headers": headers,
        "mime": "video/mp4",
    }


# ---------- 两条解析路径 ----------

async def expand_short_link(code: str, cookie: str | None = None) -> str | None:
    """v.douyin.com 短链 302 展开：GET + follow_redirects 后取最终 URL。"""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        res = await client.get(
            f"https://v.douyin.com/{code}/",
            headers={"User-Agent": MOBILE_UA, **({"Cookie": cookie} if cookie else {})},
        )
        final_url = str(res.url)
    return final_url or None


async def resolve_via_feed(page_url: str, cookie: str | None = None) -> dict:
    aweme_id = extract_aweme_id(page_url)
    if not aweme_id:
        from urllib.parse import urlparse

        path = urlparse(page_url).path
        m = re.fullmatch(r"/([A-Za-z0-9_-]{6,})/?", path)
        if m and urlparse(page_url).hostname == "v.douyin.com":
            final = await expand_short_link(m.group(1), cookie)
            aweme_id = extract_aweme_id(final or "")
    if not aweme_id:
        raise ResolveError("not-found", "抖音链接中未找到视频 ID（仅支持视频页/短链）")

    headers = {"User-Agent": MOBILE_UA, **({"Cookie": cookie} if cookie else {})}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        res = await client.get(FEED_API.format(aweme_id=aweme_id), headers=headers)
    if res.status_code != 200:
        raise ResolveError("blocked", f"抖音接口返回 HTTP {res.status_code}")
    try:
        payload = res.json()
    except (json.JSONDecodeError, ValueError):
        raise ResolveError("blocked", "抖音接口返回非 JSON 响应")
    return parse_feed(payload, aweme_id, cookie)


def resolve_via_ytdlp(page_url: str) -> dict:
    """yt-dlp 优先路径；任何失败向上抛，由 resolve() 回落 feed。"""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)
    url = info.get("url")
    if not url:
        fmts = info.get("formats") or []
        url = (fmts[-1].get("url") if fmts else None) or ""
    if not url:
        raise ResolveError("not-found", "yt-dlp 未解出视频地址")
    vid = extract_aweme_id(page_url) or str(info.get("id", ""))
    return {
        "external_key": f"{SITE}:video_id={vid}",
        "title": str(info.get("title") or f"抖音视频 {vid}")[:300],
        "duration_s": round(info["duration"]) if info.get("duration") else None,
        "poster_url": info.get("thumbnail"),
        "download_url": url,
        "download_headers": {"User-Agent": MOBILE_UA},
        "mime": "video/mp4",
    }


async def resolve(page_url: str, cookie: str | None = None) -> dict:
    try:
        return await asyncio.to_thread(resolve_via_ytdlp, page_url)
    except ResolveError:
        raise
    except Exception:
        return await resolve_via_feed(page_url, cookie)


def resolve_sync(page_url: str, cookie: str | None = None) -> dict:
    return asyncio.run(resolve(page_url, cookie))
