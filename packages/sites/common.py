"""站点 extractor 共享类型：统一产出形状与结构化解析失败。

每个 extractor 的 resolve()/parse_* 产出同一 dict：
    external_key       "站点:站点内唯一标识"（§5.5 去重键）
    title              str
    duration_s         int | None（秒）
    poster_url         str | None
    download_url       str（直链，敏感值——永不进日志/dispatch payload）
    download_headers   dict（下载直链所需头，至少 User-Agent）
    mime               "video/mp4"（解析类站点均为视频）
"""


class ResolveError(Exception):
    """结构化解析失败；code 对齐旧主仓 resolvers/types.ts 的四分类。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # not-found | blocked | quota | unsupported-form


BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def parse_hms(text: str | None) -> int | None:
    """`HH:MM:SS` → 秒；无命中返回 None。"""
    import re

    if not text:
        return None
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
