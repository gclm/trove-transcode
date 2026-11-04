"""trove API 客户端（主仓 /api/v1）。只用标准库，runner 无需额外依赖。

约定：
- 全部 Bearer 部署 key；
- 机器端点：GET /tasks/:id/fetch-info、GET /tasks/:id/artifact、PUT /tasks/:id/hls、
  PUT /tasks/:id/result、PATCH /tasks/:id、POST /external-keys/queries；
- 敏感值（直链 / 标题 / 预签名 URL）只在本进程内存里流转，永不写日志。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "trove-worker/1.0"


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def require_env(keys: list[str], env: dict | None = None) -> list[str]:
    """返回缺失的键名列表（保持入参顺序）。env 默认取 os.environ。"""
    source = os.environ if env is None else env
    return [k for k in keys if not source.get(k)]


def env_or_die(keys: list[str], env: dict | None = None) -> None:
    """自检：缺失即打印完整清单并以 2 退出（供 workflow 的 --dry-run 使用）。"""
    missing = require_env(keys, env)
    if missing:
        import sys

        print("missing required env: " + ", ".join(missing), file=sys.stderr)
        raise SystemExit(2)


class TroveApi:
    def __init__(self, base: str, key: str, timeout: int = 60):
        self.base = base.rstrip("/")
        self.key = key
        self.timeout = timeout

    # ---- 传输层 -------------------------------------------------------------

    def _request(self, method: str, path: str, body=None, *, follow: bool = True) -> tuple[int, bytes, dict]:
        data = None
        headers = {"Authorization": f"Bearer {self.key}", "User-Agent": USER_AGENT}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            # follow=False 时 3xx 是正常结果（artifact 的预签名 Location），不当错误抛。
            if not follow and 300 <= e.code < 400:
                return e.code, b"", dict(e.headers)
            payload = e.read()
            raise ApiError(e.code, _error_code(payload), _error_message(payload)) from None

    def _json(self, method: str, path: str, body=None) -> dict:
        status, raw, _ = self._request(method, path, body)
        if status == 204 or not raw:
            return {}
        return json.loads(raw.decode() or "{}")

    # ---- 任务 ---------------------------------------------------------------

    def fetch_info(self, source_id: str) -> dict:
        return self._json("GET", f"/api/v1/tasks/{source_id}/fetch-info").get("data") or {}

    def patch(self, source_id: str, **fields) -> None:
        body = {k: v for k, v in fields.items() if v is not None}
        if body:
            self._request("PATCH", f"/api/v1/tasks/{source_id}", body)

    def put_hls(self, source_id: str, payload: dict) -> dict:
        return self._json("PUT", f"/api/v1/tasks/{source_id}/hls", payload).get("data") or {}

    def put_result(self, source_id: str, payload: dict) -> dict:
        return self._json("PUT", f"/api/v1/tasks/{source_id}/result", payload).get("data") or {}

    def artifact_url(self, source_id: str) -> str | None:
        """R2 暂存对象的预签名直链（302 的 Location）；任务无 r2_key 时返回 None。"""
        try:
            _, _, headers = self._request("GET", f"/api/v1/tasks/{source_id}/artifact", follow=False)
        except ApiError as e:
            if e.status in (404, 410):
                return None
            raise
        return headers.get("Location") or headers.get("location")

    def external_keys(self, keys: list[str]) -> list[dict]:
        data = self._json("POST", "/api/v1/external-keys/queries", {"keys": keys}).get("data") or []
        return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _error_code(raw: bytes) -> str:
    try:
        return (json.loads(raw.decode()).get("error") or {}).get("code") or "unknown"
    except Exception:
        return "unknown"


def _error_message(raw: bytes) -> str:
    try:
        return (json.loads(raw.decode()).get("error") or {}).get("message") or raw.decode()[:200]
    except Exception:
        return raw.decode(errors="replace")[:200]


def quote_path(name: str) -> str:
    return urllib.parse.quote(name, safe="")


def retry(fn, *, attempts: int = 3, base_delay: float = 2.0, on_error=None):
    """线性退避重试；最后一次失败原样抛出。"""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - 重试边界
            last = e
            if on_error:
                on_error(e, i)
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    raise last  # type: ignore[misc]
