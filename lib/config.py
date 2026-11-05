"""Runner bootstrap: fetch every Telegram/resolver credential from the Worker.

GitHub Actions keeps only two secrets for the runner:
- ``API_BASE``      Worker base URL
- ``TROVE_API_KEY`` deployment key

Everything else (Telegram app id/hash, storage channel, bot roster, resolver
cookies) is stored in the Worker and fetched from ``GET /api/v1/runner-config``
with that deployment key. Values are injected into this process's environment
so the existing ``lib.bots`` / ``lib.tg`` / ``lib.resolve`` code paths keep
working; they are never written to disk or logs.
"""
from __future__ import annotations

import json
import os

from lib import api


class RunnerConfigError(RuntimeError):
    pass


def apply(data: dict) -> None:
    """Map the Worker response onto the env names used by the runner libs."""
    if not isinstance(data, dict):
        raise RunnerConfigError("runner-config response is not an object")

    bots = data.get("bots")
    if not isinstance(bots, list) or not bots:
        raise RunnerConfigError("runner-config returned no bots")

    mapping: dict[str, str] = {
        "TG_API_ID": str(data.get("tg_api_id") or ""),
        "TG_API_HASH": str(data.get("tg_api_hash") or ""),
        "TG_CHANNEL": str(data.get("tg_channel") or ""),
        "BOTS": json.dumps(bots, ensure_ascii=False),
        "TG_TOPICS": json.dumps(data.get("tg_topics") or {}, ensure_ascii=False),
    }
    cookies = data.get("cookies") or {}
    for key in ("PORN91_COOKIE", "DOUYIN_COOKIE", "XHS_COOKIE", "HAIJIAO_COOKIE"):
        value = cookies.get(key)
        if value:
            mapping[key] = str(value)

    missing = api.require_env(["TG_API_ID", "TG_API_HASH", "TG_CHANNEL", "BOTS"], mapping)
    if missing:
        raise RunnerConfigError("runner-config missing: " + ", ".join(missing))
    os.environ.update(mapping)


def bootstrap(client: api.TroveApi) -> dict:
    """Fetch and apply the Worker-owned runtime config."""
    data = client.runner_config()
    apply(data)
    return data
