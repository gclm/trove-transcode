"""解析 worker：一个链接 → 元数据回报（plan §5）。

流程：GET fetch-info 取 source_url → packages/sites 解析 → PATCH /tasks/:id 回写元数据。
解析失败也回报 error_code，让主仓状态机落到 error 而不是挂在 resolving。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib import api, resolve  # noqa: E402
from packages.sites import registry  # noqa: E402

CORE = ["SOURCE_ID", "API_BASE", "TROVE_API_KEY"]
# 自检清单比运行期需求宽：runner 上 TG 凭据缺失同样会让后续上传失败，
# 所以 --dry-run 一次把整条流水线需要的键都报出来。
SELFCHECK = CORE + ["TG_API_ID", "TG_API_HASH", "TG_BOT_TOKEN", "TG_CHANNEL"]


def main() -> int:
    if "--dry-run" in sys.argv:
        api.env_or_die(SELFCHECK)
        return 0
    api.env_or_die(CORE)
    source_id = os.environ["SOURCE_ID"]
    client = api.TroveApi(os.environ["API_BASE"], os.environ["TROVE_API_KEY"])

    try:
        info = client.fetch_info(source_id)
        link = info.get("source_url") or info.get("url") or ""
        if not link:
            raise registry.ResolveError("unsupported-form", "task has no source_url")
        client.patch(source_id, status="resolving")
        meta = resolve.resolve(link)
        client.patch(
            source_id,
            status="resolved",
            name=meta.get("title"),
            duration_s=meta.get("duration_s"),
            poster_url=meta.get("poster_url"),
            external_key=meta.get("external_key"),
            site=meta.get("site") or resolve.site_of(link),
        )
        print(json.dumps({"ok": True, "source_id": source_id}, ensure_ascii=False))
        return 0
    except registry.ResolveError as e:
        client.patch(source_id, status="error", error_code=e.code, error_message=str(e)[:300])
        print(json.dumps({"ok": False, "code": e.code}, ensure_ascii=False))
        return 1
    except Exception as e:  # noqa: BLE001 - 兜底回报，避免任务悬挂
        client.patch(source_id, status="error", error_code="resolve_failed", error_message=str(e)[:300])
        print(json.dumps({"ok": False, "code": "resolve_failed"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
