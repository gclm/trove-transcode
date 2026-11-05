"""91porn 定时采集编排 job（plan §5.6 第一步）。

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

from lib import api, config  # noqa: E402
from workers.scheduled.collect91.shards import shard_items  # noqa: E402

CORE = ["API_BASE", "TROVE_API_KEY"]
HOT_LIMIT = int(os.environ.get("COLLECT_LIMIT", "72"))
FOLDER_NAME = os.environ.get("COLLECT_FOLDER", "91porn")
# 6 个上传 bot 配置好后设为 "0,1,2,3,4,5"；默认只用主 bot，避免未配置的槽位失败。
BOT_SLOTS = [int(x) for x in os.environ.get("COLLECT_BOT_SLOTS", "0").split(",") if x.strip()]
_91 = importlib.import_module("packages.sites.91porn")


def _folder_id(client, name: str = FOLDER_NAME) -> str:
    """采集产物统一落「91porn」目录：先按名字找，找不到就建。"""
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
    config.bootstrap(client)

    keys = _91.list_hot(cookie=os.environ.get("PORN91_COOKIE"), limit=HOT_LIMIT)
    if not keys:
        print("[]")
        return 0

    # 一次 batch 建任务：服务端按 stored/in_flight/请求内重复去重，返回 created/skipped。
    items = []
    for key in keys:
        entry = _91.entry_from_key(key)
        if entry:
            items.append({"url": entry, "external_key": key, "source_site": "91porn", "source_tag": "当前最热"})
    if not items:
        print("[]")
        return 0

    folder_id = _folder_id(client)
    result = client.create_tasks_batch(items, folder_id=folder_id)
    created = result.get("created") or []
    skipped = result.get("skipped") or []
    print(f"batch created={len(created)} skipped={len(skipped)}", file=sys.stderr)
    if skipped:
        by_state: dict[str, int] = {}
        for row in skipped:
            by_state[row.get("state", "unknown")] = by_state.get(row.get("state", "unknown"), 0) + 1
        print(f"batch skipped states={by_state}", file=sys.stderr)

    shard = [{"source_id": row["id"], "key": row.get("external_key") or ""} for row in created if row.get("id")]
    summary_path = os.environ.get("COLLECT_SUMMARY_PATH")
    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump({"fresh": len(created), "created": len(created), "skipped": len(skipped), "items": len(shard)}, fh)
    print(json.dumps(shard_items(shard, slots=BOT_SLOTS), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
