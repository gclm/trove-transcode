"""91pron 定时采集编排 job（plan §5.6 第一步）。

只做编排，不下载：热点列表 → 去重 → 建 resolve 任务 → 输出 matrix 分片 JSON。
真正下载/转码/上传在 pipeline job 里并行跑（每个分片一个 runner）。

--dry-run：只做 env 自检，输出空分片 `[]`，不触网。
"""
from __future__ import annotations

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from lib import api  # noqa: E402
from workers.scheduled.collect91.shards import shard_items  # noqa: E402

CORE = ["API_BASE", "TROVE_API_KEY"]
HOT_LIMIT = int(os.environ.get("COLLECT_LIMIT", "72"))
FOLDER_NAME = os.environ.get("COLLECT_FOLDER", "91pron")
_91 = importlib.import_module("packages.sites.91porn")


def _folder_id(client, name: str = FOLDER_NAME) -> str:
    """采集产物统一落「91pron」目录：先按名字找，找不到就建。"""
    for folder in client.folders():
        if folder.get("name") == name:
            return folder["id"]
    return client.create_folder(name).get("id") or ""


def main() -> int:
    if "--dry-run" in sys.argv:
        api.env_or_die(CORE)
        print("[]")
        return 0
    api.env_or_die(CORE)
    client = api.TroveApi(os.environ["API_BASE"], os.environ["TROVE_API_KEY"])

    keys = _91.list_hot(cookie=os.environ.get("PORN91_COOKIE"), limit=HOT_LIMIT)
    if not keys:
        print("[]")
        return 0

    # 去重：stored（已入库）/ in_flight（在途任务）都跳过，只有 absent 才建任务。
    states = {row["key"]: row["state"] for row in client.external_keys(keys)}
    fresh = [k for k in keys if states.get(k) == "absent"]

    folder_id = _folder_id(client)
    shard = []
    for key in fresh:
        entry = _91.entry_from_key(key)
        if not entry:
            continue
        try:
            task = client._json("POST", "/api/v1/tasks", {"url": entry, "folder_id": folder_id}).get("data") or {}
        except api.ApiError as e:
            print(f"skip {key}: {e.code}", file=sys.stderr)
            continue
        if task.get("id"):
            shard.append({"source_id": task["id"], "key": key})

    print(json.dumps(shard_items(shard), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
