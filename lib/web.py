"""带 cookie jar 的页面抓取：自动拿站点游客 cookie，免人工配置。

91porn 这类站点在没有 visitor cookie 时会把「游客每日观看次数」算在 IP 上，
GitHub Actions 的共享出口 IP 很容易被判超额；先访问一次首页让服务端种下
`CLIPSHARE` 等 cookie，再带着它请求目标页，即可按「新访客」计数。其他站点
（小红书/海角）同样在首页种 cookie，统一走这里。
"""
from __future__ import annotations

import httpx
from urllib.parse import urlparse

from packages.sites.common import BROWSER_UA

# 站点 → 需要先访问一次以领取游客 cookie 的首页
WARM_UP = {
    "91porn.com": "https://www.91porn.com/index.php",
    "9p234.com": "https://903.9p234.com/index.php",
    "xiaohongshu.com": "https://www.xiaohongshu.com/",
    "haijiao.com": "https://www.haijiao.com/",
}


def _home_for(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    for suffix, home in WARM_UP.items():
        if host == suffix or host.endswith("." + suffix):
            return home
    return None


async def fetch_page(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    warm_up: bool = True,
    attempts: int = 2,
) -> httpx.Response:
    """GET 一个页面；同一 Client 内先访问首页领 cookie，再取目标页。

    每次 attempt 都用全新 Client（= 全新 cookie jar），配额页/5xx 时重试一次，
    相当于换一个「访客身份」。
    """
    res: httpx.Response | None = None
    for _ in range(max(attempts, 1)):
        hdrs = {"User-Agent": BROWSER_UA}
        if headers:
            hdrs.update(headers)
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers=hdrs) as client:
            if warm_up:
                home = _home_for(url)
                if home:
                    try:
                        await client.get(home)
                    except httpx.HTTPError:
                        pass  # 首页异常不阻塞目标页
            res = await client.get(url)
        if res.status_code < 500:
            break
    assert res is not None
    return res
