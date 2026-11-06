"""91porn extractor。

移植真源：旧 scripts/lib/resolvers.mjs resolve91porn + 主仓 server/resolvers/porn91.ts
（主仓只读参考）。91porn 的 Cloudflare 拦 Worker 出口 IP 但放行 GitHub Actions
runner；页面把直链藏在 document.write(strencode2("<percent-encoded <source> tag>"))
里，strencode2 即 plain unescape（verified 2026-11-02）。

模块名以数字开头，只能 importlib.import_module("packages.sites.91porn") 加载。
"""

import base64
import os
import re
from urllib.parse import quote, unquote, urljoin, urlparse, parse_qs

import httpx

from .common import BROWSER_UA, ResolveError, parse_hms

SITE = "91porn"
# 入口按顺序探测，先通先用：91porn.com → www.91porn.com → 903.9p234.com。
# SITE_91PORN_BASE 可强制指定首选入口；2026-11-06 实测镜像对 GitHub runner 403，
# 原域名正常，脚本会在运行时自动切换，不依赖固定域名。
DEFAULT_BASES = ("https://91porn.com", "https://www.91porn.com", "https://903.9p234.com")
BASE_URL = os.environ.get("SITE_91PORN_BASE", DEFAULT_BASES[0]).rstrip("/")


def base_candidates() -> list[str]:
    """返回候选入口：显式覆盖优先，其后按默认顺序补齐、去重。"""
    out: list[str] = []
    override = os.environ.get("SITE_91PORN_BASE", "").rstrip("/")
    if override:
        out.append(override)
    for base in DEFAULT_BASES:
        if base not in out:
            out.append(base)
    return out


def _base_of(page_url: str) -> str:
    parsed = urlparse(page_url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else BASE_URL


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
    # 91porn 默认给英文标题；中文标题需要语言 cookie。
    headers["Cookie"] = cookie or "language=cn_CN; over18=1; CLIPSHARE=1"
    return headers


def extract_id(page_url: str) -> str | None:
    q = parse_qs(urlparse(page_url).query)
    return (q.get("viewkey") or [None])[0]


def can_handle(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("91porn.com", "www.91porn.com", "9p234.com") or host.endswith(".9p234.com")


def entry_from_key(external_key: str, base: str | None = None) -> str:
    vid = external_key.split("viewkey=", 1)[1]
    return f"{(base or BASE_URL).rstrip('/')}/view_video.php?viewkey={vid}"


def _decode_hidden(value: str, mode: int, key: str) -> str:
    if mode == 1:
        raw = bytes.fromhex(value)
    elif mode == 2:
        raw = base64.b64decode(value[::-1])
    else:
        raw = base64.b64decode(value)
    kb = key.encode()
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(raw)).decode("utf-8", "replace")


def decode_page_title(html: str) -> str | None:
    """解 91porn 的隐藏标题 payload（2026-11 反爬版本）。"""
    m = re.search(r'<i[^>]*data-ba760="([^"]+)"', html, re.I)
    if not m:
        return None
    script = re.search(r'var m=(\d+),id="[^"]+",at="data-ba760",k="([^"]+)"', html)
    if not script:
        return None
    title = _decode_hidden(m.group(1), int(script.group(1)), script.group(2))
    return re.sub(r"[\u200b-\u200f\u2060\ufeff\ufe00-\ufe0f]", "", title).strip()



def _clean_title(value: str | None) -> str | None:
    """清理站点标题噪声：零宽字符、反爬提示、推广尾巴与 URL。"""
    if not value:
        return None
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[\u200b-\u200f\u2060\ufeff\ufe00-\ufe0f]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    for marker in ("91版主提醒您", "91版主提醒", "请到91P945", "请到 91P945"):
        if marker in value:
            value = value.split(marker)[0].strip()
    value = re.sub(r"https?://\S+", "", value, flags=re.I)
    value = re.sub(r"\b(?:t\.me|www\.)\S+", "", value, flags=re.I)
    value = re.sub(r"\s*(?:电报|TG|Telegram|QQ|微信|私信|完整版|购买|下单|频道|资源群)[:：✈\s]*$", "", value, flags=re.I).strip()
    value = re.sub(r"\s*[-–—]\s*91porn\s*$", "", value, flags=re.I).strip()
    value = value.strip(" -–—|·:：,，。;；!?！？")
    if not value or len(value) < 2:
        return None
    if any(x in value for x in ("精彩视频不断更新", "违法91规定", "Free video", "Free  video", "91porn")):
        return None
    return value


def _abs_url(base: str, value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return urljoin(base, value)
    return value


def _parse_author(html: str, base: str) -> tuple[str | None, str | None]:
    """详情页 From 区：author 与 uprofile.php?UID=... 主页。"""
    m = re.search(
        r'From:\s*</span>\s*<span[^>]*class=["\']title-yakov["\'][^>]*>\s*'
        r'<a[^>]+href=["\']([^"\']*uprofile\.php[^"\']*)["\'][^>]*>.*?'
        r'<span[^>]*class=["\']title["\'][^>]*>(.*?)</span>',
        html, re.I | re.S,
    )
    if not m:
        m = re.search(r'From:\s*</span>\s*<a[^>]+href=["\']([^"\']*uprofile\.php[^"\']*)["\'][^>]*>(.*?)</a>', html, re.I | re.S)
    if not m:
        return None, None
    author = _clean_title(re.sub(r"<[^>]+>", " ", m.group(2)))
    return author, _abs_url(base, m.group(1))


def _parse_published_at(html: str) -> str | None:
    m = re.search(r'Added:\s*</span>\s*<span[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</span>', html, re.I)
    if not m:
        m = re.search(r'Added:\s*(\d{4}-\d{2}-\d{2})', html, re.I)
    return f"{m.group(1)}T00:00:00" if m else None


def _parse_cover(html: str, base: str) -> str | None:
    poster = _re_search(r'<video[^>]*\bposter=["\']([^"\']+)', html, re.I)
    return _abs_url(base, poster)


def _video_url_from_html(html: str) -> str | None:
    cipher = (
        _re_search(r'strencode2\("([^"]+)"\)', html)
        or _re_search(r'document\.write\("([^"]+)"\)', html)
    )
    if not cipher:
        return None
    try:
        source_tag = unquote(cipher)
    except Exception:
        return None
    return _re_search(r"src=['\"]([^'\"]+)", source_tag, re.I)

def parse_page(html: str, page_url: str) -> dict:
    title_m = re.search(r"<title>([^<]+)</title>", html, re.I)
    page_title = _clean_title(title_m.group(1) if title_m else None)
    # 标题优先级：页面 <title>（通常是真实标题）→ 隐藏 payload → og → v_desc。
    hidden_title = _clean_title(decode_page_title(html))
    og = _clean_title(_re_search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I))
    desc = _clean_title(_re_search(r'id=v_desc[^>]*>(.*?)</div>', html, re.I | re.S))
    candidates = [page_title, hidden_title, og, desc]
    title = next((v for v in candidates if v), "91porn video")

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

    page_base = _base_of(page_url)
    poster = _parse_cover(html, page_base)
    # 时长有两种页面形态：`时长: 00:15:24` 与 `<span class="video-info-span">00:15:24</span>`。
    hidden_duration = None
    hm = re.search(r'<i[^>]*data-ba760d="([^"]+)"', html, re.I)
    hm_script = re.search(r'var m=(\d+),id="[^"]+",at="data-ba760",k="([^"]+)"', html)
    if hm and hm_script:
        try:
            hidden_duration = int(_decode_hidden(hm.group(1), int(hm_script.group(1)), hm_script.group(2)))
        except Exception:
            hidden_duration = None
    duration = hidden_duration or parse_hms(
        _re_search(r"video-info-span[^>]*>\s*(\d{1,2}:\d{2}:\d{2})", html)
        or _re_search(r"时长[^<]*(?:<span[^>]*>)?\s*(\d{1,2}:\d{2}:\d{2})", html)
    )

    author, author_url = _parse_author(html, page_base)
    published_at = _parse_published_at(html)
    vid = extract_id(page_url)
    display_title = re.sub(r"\.mp4$", "", title, flags=re.I).strip() or "91porn video"
    # 91porn 的文件名跟页面标题走，扩展名固定 .mp4（用户 2026-11-04 定的规则）。
    safe_name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", display_title).strip() or "91porn video"
    return {
        "external_key": f"{SITE}:viewkey={vid}" if vid else f"{SITE}:unknown",
        "title": display_title,
        "file_name": f"{safe_name}.mp4",
        "duration_s": duration,
        "poster_url": poster,
        "cover_url": poster,
        "author": author,
        "author_url": author_url,
        "published_at": published_at,
        "source_url": page_url,
        "download_url": video_url,
        "download_headers": {"User-Agent": BROWSER_UA, "Referer": f"{page_base}/"},
        "mime": "video/mp4",
    }


def _re_search(pattern, text, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


async def resolve(page_url: str, cookie: str | None = None) -> dict:
    from lib.web import fetch_page

    vid = extract_id(page_url)
    bases: list[str] = []
    origin = _base_of(page_url)
    if origin:
        bases.append(origin)
    for base in base_candidates():
        if base not in bases:
            bases.append(base)

    last: ResolveError | None = None
    for base in bases:
        target = f"{base}/view_video.php?viewkey={vid}" if vid else page_url
        for _ in range(2):
            res = await fetch_page(target, headers=page_headers(cookie))
            if res.status_code != 200:
                last = ResolveError("blocked", f"91porn returned HTTP {res.status_code} ({base})")
                continue
            try:
                meta = parse_page(res.text, target)
                # HD 页通常由播放器 JS 注入；静态解析不到时保留 SD，不能因此判失败。
                try:
                    hd_url = target.replace("/view_video.php?", "/view_video_hd.php?")
                    if hd_url != target:
                        hd_res = await fetch_page(hd_url, headers=page_headers(cookie))
                        if hd_res.status_code == 200:
                            hd_src = _video_url_from_html(hd_res.text)
                            if hd_src:
                                meta["download_url"] = hd_src
                                meta["quality_hint"] = "hd"
                except Exception:
                    pass
                return meta
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


async def pick_base(cookie: str | None = None, category: str = "tf") -> str:
    """按候选顺序探测列表页，返回第一个可访问的入口。"""
    from lib.web import fetch_page

    errors: list[str] = []
    for base in base_candidates():
        url = f"{base}/v.php?category={quote(category)}&viewtype=basic&page=1"
        try:
            res = await fetch_page(url, headers=page_headers(cookie))
            if res.status_code == 200 and _extract_viewkeys(res.text):
                return base
            errors.append(f"{base} HTTP {res.status_code}")
        except Exception as exc:  # noqa: BLE001 - 换下一个入口
            errors.append(f"{base} {type(exc).__name__}")
    raise ResolveError("blocked", "91porn 入口均不可用: " + "; ".join(errors[:4]))


def pick_base_sync(cookie: str | None = None, category: str = "tf") -> str:
    import asyncio

    return asyncio.run(pick_base(cookie, category))


async def _fetch_list_page(base: str, category: str, page: int, cookie: str | None) -> str:
    from lib.web import fetch_page

    url = f"{base}/v.php?category={quote(category)}&viewtype=basic&page={page}"
    res = await fetch_page(url, headers=page_headers(cookie))
    if res.status_code != 200:
        raise ResolveError("blocked", f"91porn {category} page {page} HTTP {res.status_code}")
    return res.text


def list_category(
    base: str,
    category: str = "tf",
    cookie: str | None = None,
    limit: int = 72,
    pages: int = HOT_PAGES,
) -> list[str]:
    """抓指定分类的前 N 页，按 viewkey 跨页去重。"""
    import asyncio

    async def collect() -> list[str]:
        seen: list[str] = []
        for page in range(1, pages + 1):
            html = await _fetch_list_page(base, category, page, cookie)
            for key in _extract_viewkeys(html):
                if key not in seen:
                    seen.append(key)
                if len(seen) >= limit:
                    return seen
        return seen

    keys = asyncio.run(collect())
    if not keys:
        raise ResolveError("blocked", f"91porn {category} 列表未解出任何 viewkey")
    return keys


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

    url = f"{BASE_URL}/v.php?category=hot&viewtype=basic&page={page}"
    res = await fetch_page(url, headers=page_headers(cookie))
    if res.status_code != 200:
        raise ResolveError("blocked", f"91porn hot page {page} HTTP {res.status_code}")
    return res.text


async def _fetch_home(cookie: str | None) -> str:
    from lib.web import fetch_page

    res = await fetch_page(f"{BASE_URL}/index.php", headers=page_headers(cookie))
    if res.status_code != 200:
        raise ResolveError("blocked", f"91porn homepage HTTP {res.status_code}")
    return res.text


def resolve_sync(page_url: str, cookie: str | None = None) -> dict:
    import asyncio

    return asyncio.run(resolve(page_url, cookie))
