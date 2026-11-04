"""haijiao（海角社区）extractor。

无旧实现可移植（主仓/旧 mjs 均无此站点），为设计新增：帖子页为论坛 HTML，
视频源优先级 <video><source> → m3u8 直链 → a[href$=.mp4]。域名常更换，
can_handle 用域名关键词匹配；结构待真机 fixture 校准（plan 执行记录）。
"""

import asyncio
import re
from urllib.parse import urlparse

import httpx

from .common import BROWSER_UA, ResolveError

SITE = "haijiao"
TID_RE = re.compile(r"thread-(\d+)-")
KNOWN_HOSTS = ("hjd2048.com", "haijiao")


def extract_post_id(url: str) -> str | None:
    """帖子 id：thread-<id>-1-1.html 里的数字。"""
    m = TID_RE.search(urlparse(url).path) or TID_RE.search(url)
    return m.group(1) if m else None


# 兼容旧命名（内部调用点仍写 extract_id）。
extract_id = extract_post_id


def can_handle(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(k in host for k in KNOWN_HOSTS)


def entry_from_key(external_key: str) -> str:
    post_id = external_key.split("post_id=", 1)[1]
    return f"https://hjd2048.com/2048/thread-{post_id}-1-1.html"


def parse_page(html: str, page_url: str) -> dict:
    post_id = extract_post_id(page_url) or ""
    title_m = re.search(r"<title>([^<]+)</title>", html, re.I)
    title = re.sub(r"\s*[-–|]\s*海角.*$", "", title_m.group(1) if title_m else "").strip()

    video_url = None
    m = re.search(r"<video[^>]*>\s*<source[^>]+src=\"([^\"]+)\"", html, re.I)
    if m:
        video_url = m.group(1)
    if not video_url:
        m = re.search(r"<video[^>]+src=\"([^\"]+)\"", html, re.I)
        if m:
            video_url = m.group(1)
    if not video_url:
        m = re.search(r"[\"'](https?://[^\"']+\.m3u8[^\"']*)[\"']", html, re.I)
        if m:
            video_url = m.group(1)
    if not video_url:
        m = re.search(r"href=\"(https?://[^\"]+\.mp4[^\"]*)\"", html, re.I)
        if m:
            video_url = m.group(1)
    if not video_url:
        raise ResolveError("not-found", "海角帖子页未找到视频源（附件或结构变化需校准）")

    if video_url.startswith("//"):
        video_url = "https:" + video_url

    return {
        "external_key": f"{SITE}:post_id={post_id}" if post_id else f"{SITE}:unknown",
        "title": title or "haijiao video",
        "duration_s": None,  # 帖子页无时长元数据
        "poster_url": None,
        "download_url": video_url,
        "download_headers": {
            "User-Agent": BROWSER_UA,
            "Referer": f"{urlparse(page_url).scheme}://{urlparse(page_url).hostname}/",
        },
        "mime": "video/mp4",
    }


async def resolve(page_url: str, cookie: str | None = None) -> dict:
    headers = {"User-Agent": BROWSER_UA, **({"Cookie": cookie} if cookie else {})}
    from lib.web import fetch_page

    res = await fetch_page(page_url, headers=headers)
    if res.status_code != 200:
        raise ResolveError("blocked", f"haijiao HTTP {res.status_code}")
    return parse_page(res.text, page_url)


def resolve_sync(page_url: str, cookie: str | None = None) -> dict:
    return asyncio.run(resolve(page_url, cookie))
