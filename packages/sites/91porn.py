"""91porn extractor。

移植真源：旧 scripts/lib/resolvers.mjs resolve91porn + 主仓 server/resolvers/porn91.ts
（主仓只读参考）。91porn 的 Cloudflare 拦 Worker 出口 IP 但放行 GitHub Actions
runner；页面把直链藏在 document.write(strencode2("<percent-encoded <source> tag>"))
里，strencode2 即 plain unescape（verified 2026-11-02）。

模块名以数字开头，只能 importlib.import_module("packages.sites.91porn") 加载。
"""

import re
from urllib.parse import quote, unquote, urlparse, parse_qs

import httpx

from .common import BROWSER_UA, ResolveError, parse_hms

SITE = "91porn"
ENTRY_URL = "https://www.91porn.com/view_video.php?viewkey={vid}"


def page_headers(cookie: str | None = None) -> dict:
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def extract_id(page_url: str) -> str | None:
    q = parse_qs(urlparse(page_url).query)
    return (q.get("viewkey") or [None])[0]


def can_handle(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("91porn.com", "www.91porn.com")


def entry_from_key(external_key: str) -> str:
    vid = external_key.split("viewkey=", 1)[1]
    return ENTRY_URL.format(vid=vid)


def parse_page(html: str, page_url: str) -> dict:
    title_m = re.search(r"<title>([^<]+)</title>", html, re.I)
    title = re.sub(r"\s*[-–]\s*91porn\s*$", "", title_m.group(1) if title_m else "").strip()

    # 200 但无播放器：反爬拦截或游客每日配额页。
    if "strencode2" not in html and "player_one" not in html:
        if re.search(r"temporarily unable|please try again", html, re.I):
            raise ResolveError("blocked", "91porn 反爬拦截：请稍后重试")
        if re.search(r"观看|次数|每天|login", html):
            raise ResolveError("quota", "91porn 游客配额已用尽（已自动更换访客身份仍失败，稍后重试或配置 cookie）")
        raise ResolveError("not-found", "91porn 页面中未找到播放器（视频不存在或已删除）")

    cipher = (
        _re_search(r'strencode2\("([^"]+)"\)', html)
        or _re_search(r'document\.write\("([^"]+)"\)', html)
    )
    if not cipher:
        raise ResolveError("blocked", "91porn 播放器加密形态已变化，需要更新解析插件")
    try:
        source_tag = unquote(cipher)
    except Exception:
        raise ResolveError("blocked", "91porn 播放地址解码失败，需要更新解析插件")
    video_url = _re_search(r"src='([^']+)'", source_tag, re.I)
    if not video_url:
        raise ResolveError("not-found", "91porn 页面未解出视频地址")

    poster = _re_search(r'<video[^>]*\bposter="([^"]+)"', html, re.I)
    # 时长有两种页面形态：`时长: 00:15:24` 与 `<span class="video-info-span">00:15:24</span>`。
    duration = parse_hms(
        _re_search(r"video-info-span[^>]*>\s*(\d{1,2}:\d{2}:\d{2})", html)
        or _re_search(r"时长[^<]*(?:<span[^>]*>)?\s*(\d{1,2}:\d{2}:\d{2})", html)
    )

    vid = extract_id(page_url)
    display_title = title or "91porn video"
    # 91porn 的文件名跟页面标题走，扩展名固定 .mp4（用户 2026-11-04 定的规则）。
    safe_name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", display_title).strip() or "91porn video"
    return {
        "external_key": f"{SITE}:viewkey={vid}" if vid else f"{SITE}:unknown",
        "title": display_title,
        "file_name": f"{safe_name}.mp4",
        "duration_s": duration,
        "poster_url": poster,
        "download_url": video_url,
        "download_headers": {"User-Agent": BROWSER_UA, "Referer": "https://91porn.com/"},
        "mime": "video/mp4",
    }


def _re_search(pattern, text, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


async def resolve(page_url: str, cookie: str | None = None) -> dict:
    from lib.web import fetch_page

    last: ResolveError | None = None
    for _ in range(2):
        res = await fetch_page(page_url, headers=page_headers(cookie))
        if res.status_code != 200:
            last = ResolveError("blocked", f"91porn returned HTTP {res.status_code}")
            continue
        try:
            return parse_page(res.text, page_url)
        except ResolveError as exc:
            last = exc
            if exc.code not in ("quota", "blocked"):
                raise
    assert last is not None
    raise last


HOT_PAGES = 3


def _extract_viewkeys(html: str) -> list[str]:
    """从一页列表 HTML 提取去重 viewkey（页面存在重复容器与广告格）。"""
    seen: list[str] = []
    for m in re.finditer(r"view_video\.php\?viewkey=([0-9a-zA-Z]+)", html):
        key = f"{SITE}:viewkey={m.group(1)}"
        if key not in seen:
            seen.append(key)
    return seen


def list_hot(cookie: str | None = None, limit: int = 72, pages: int = HOT_PAGES) -> list[str]:
    """抓 hot1/hot2/hot3 三页热点列表，返回去重后的 external_key 列表。

    每页约 24 个唯一 viewkey，三页合计 72 个（plan 实测：44/41/39 个容器，
    去重后 24/24/24）。列表页用宽松 viewkey 提取，零命中抛 blocked。
    """
    import asyncio

    async def collect() -> list[str]:
        seen: list[str] = []
        for page in range(1, pages + 1):
            html = await _fetch_hot_page(page, cookie)
            for key in _extract_viewkeys(html):
                if key not in seen:
                    seen.append(key)
                if len(seen) >= limit:
                    return seen
        return seen

    keys = asyncio.run(collect())
    if not keys:
        raise ResolveError("blocked", "91porn 热点列表未解出任何 viewkey，列表页结构需校准")
    return keys


async def _fetch_hot_page(page: int, cookie: str | None) -> str:
    from lib.web import fetch_page

    url = f"https://www.91porn.com/v.php?category=hot&viewtype=basic&page={page}"
    res = await fetch_page(url, headers=page_headers(cookie))
    if res.status_code != 200:
        raise ResolveError("blocked", f"91porn hot page {page} HTTP {res.status_code}")
    return res.text


async def _fetch_home(cookie: str | None) -> str:
    from lib.web import fetch_page

    res = await fetch_page("https://www.91porn.com/index.php", headers=page_headers(cookie))
    if res.status_code != 200:
        raise ResolveError("blocked", f"91porn homepage HTTP {res.status_code}")
    return res.text


def resolve_sync(page_url: str, cookie: str | None = None) -> dict:
    import asyncio

    return asyncio.run(resolve(page_url, cookie))
