"""xiaohongshu extractor。

无旧实现可移植（主仓/旧 mjs 均无此站点），为设计新增：笔记页把笔记数据放
window.__INITIAL_STATE__ JSON；视频笔记直链在 noteData.data.media.videoMedia.stream
（h264 轨优先，兼容性最好）。结构待真机 fixture 校准（plan 执行记录）。
"""

import asyncio
import json
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .common import BROWSER_UA, ResolveError, parse_hms

SITE = "xiaohongshu"
NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item|user/profile/[A-Za-z0-9]+)/([0-9a-f]{16,})")
ENTRY_URL = "https://www.xiaohongshu.com/explore/{note_id}"


def extract_note_id(url: str) -> str | None:
    m = NOTE_ID_RE.search(url)
    return m.group(1) if m else None


def can_handle(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in ("www.xiaohongshu.com", "xiaohongshu.com") and extract_note_id(url):
        return True
    # xhslink.com 短链：resolve 时 302 展开后再判定
    return host == "xhslink.com"


def entry_from_key(external_key: str) -> str:
    return ENTRY_URL.format(note_id=external_key.split("note_id=", 1)[1])


def _pick_stream(stream: dict) -> tuple[str | None, int | None]:
    """h264 轨优先；条目带 master_url / video_url 与 duration。

    真机回包的 duration 是毫秒（61000），但部分接口给秒（61）：>1000 视为毫秒。
    """
    for variant_key in ("h264", "h265", "av1"):
        variants = (stream or {}).get(variant_key) or []
        if not variants:
            continue
        v = variants[0]
        url = v.get("master_url") or v.get("video_url")
        if url:
            raw_dur = v.get("duration")
            duration = (round(raw_dur / 1000) if raw_dur > 1000 else int(raw_dur)) if raw_dur else None
            return url, duration
    return None, None


def _initial_state(html: str) -> dict | None:
    """从页面里抠出 window.__INITIAL_STATE__ 的 JSON 对象。

    真实页面里 `undefined` 字面量会让 JSON 解析失败，先替换成 null；
    用 raw_decode 按值边界取对象，不依赖 `</script>` 位置。
    """
    idx = html.find("__INITIAL_STATE__")
    if idx < 0:
        return None
    start = html.find("{", idx)
    if start < 0:
        return None
    raw = re.sub(r"\bundefined\b", "null", html[start:])
    try:
        value, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_page(html: str, page_url: str) -> dict:
    note_id = extract_note_id(page_url) or ""
    state = _initial_state(html)
    if not state:
        raise ResolveError("blocked", "小红书页面未含 __INITIAL_STATE__（需登录或结构变化）")

    data = ((state.get("noteData") or {}).get("data")) or (state.get("noteData") or {}).get("note") or {}
    if not data:
        raise ResolveError("not-found", "小红书笔记数据缺失（笔记不存在或已删除）")

    media = data.get("media") or {}
    video_media = media.get("videoMedia") or {}
    url, duration = _pick_stream(video_media.get("stream"))
    if not url:
        raise ResolveError("unsupported-form", "该笔记无视频内容或直链形态未支持")

    title = str(data.get("title") or data.get("desc") or "小红书笔记")[:300]
    poster = (media.get("image") or {}).get("urlDefault") or data.get("cover", {}).get("url_default")
    if not duration:
        duration = parse_hms(str(data.get("duration") or ""))

    return {
        "external_key": f"{SITE}:note_id={note_id}",
        "title": title,
        "duration_s": duration,
        "poster_url": poster,
        "download_url": url,
        "download_headers": {"User-Agent": BROWSER_UA, "Referer": "https://www.xiaohongshu.com/"},
        "mime": "video/mp4",
    }


async def resolve(page_url: str, cookie: str | None = None) -> dict:
    headers = {"User-Agent": BROWSER_UA, **({"Cookie": cookie} if cookie else {})}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        res = await client.get(page_url, headers=headers)
        final_url = str(res.url)
    if res.status_code != 200:
        raise ResolveError("blocked", f"xiaohongshu HTTP {res.status_code}")
    # xhslink 短链展开后再提 note_id
    if extract_note_id(final_url) and extract_note_id(final_url) != extract_note_id(page_url):
        return parse_page(res.text, final_url)
    return parse_page(res.text, page_url)


def resolve_sync(page_url: str, cookie: str | None = None) -> dict:
    return asyncio.run(resolve(page_url, cookie))
