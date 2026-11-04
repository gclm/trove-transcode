"""HTTP 下载：Range 续传 + 断流重试 + 服务器忽略 Range 的降级。

为什么需要它：源站直链常见两种失败——提前断流（拿到一部分就 EOF）与
Range 不被支持。前者靠「已落盘字节数」续传，后者靠 200 响应回退重下。
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

CHUNK = 1 << 20
DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class DownloadError(RuntimeError):
    pass


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def download(
    url: str,
    dest: str,
    *,
    headers: dict | None = None,
    resume: bool = True,
    timeout: int = 120,
    attempts: int = 4,
    progress=None,
) -> dict:
    """把 url 下载到 dest，返回 {path, size, resumed_bytes, attempts}。

    - dest 已存在且 resume=True：发 `Range: bytes=<size>-` 续传；
    - 服务端返回 200（不支持 Range）：truncate 后从头写；
    - 中途 EOF：保留已写字节，下一轮继续 Range。
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    hdrs = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update({k: v for k, v in headers.items() if v})
    resumed_total = 0
    last_error: Exception | None = None

    for attempt in range(attempts):
        start = _size(dest) if resume else 0
        req_headers = dict(hdrs)
        if start > 0:
            req_headers["Range"] = f"bytes={start}-"
        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                mode = "ab"
                if start > 0 and status == 200:
                    # 服务器忽略 Range：整段重来，避免把完整文件追加到旧数据后面。
                    mode = "wb"
                    start = 0
                expected = resp.headers.get("Content-Length")
                total = start + int(expected) if expected and expected.isdigit() else None
                with open(dest, mode) as fh:
                    written = start
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(written, total)
                size = _size(dest)
                if total is not None and size < total:
                    raise DownloadError(f"truncated at {size}/{total}")
                return {
                    "path": dest,
                    "size": size,
                    "resumed_bytes": resumed_total,
                    "attempts": attempt + 1,
                }
        except (urllib.error.URLError, DownloadError, TimeoutError, ConnectionError) as e:
            last_error = e
            resumed_total = _size(dest)
            if attempt < attempts - 1:
                time.sleep(2 * (attempt + 1))
    raise DownloadError(f"download failed after {attempts} attempts: {last_error}")


def fetch_text(url: str, *, headers: dict | None = None, timeout: int = 60) -> str:
    hdrs = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")
