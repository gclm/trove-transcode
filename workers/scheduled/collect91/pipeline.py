"""采集分片流水线：一片内逐条走完「解析 → 确认 → 下载 → 转码 → 上传」。

片内串行（2 核 runner，ffmpeg 会把 CPU 吃满）；片间并行由 workflow matrix 提供。
单条失败只影响该条：记日志后继续，下一轮 dedup 会重新拾起未入库的条目。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from lib import api, resolve  # noqa: E402
from packages.sites import registry  # noqa: E402
from workers.upload.job import process as upload_process  # noqa: E402


def run_shard(client: api.TroveApi, shard: list[dict], chat_id: str) -> dict:
    ok, failed = 0, []
    for entry in shard:
        source_id = entry.get("source_id")
        if not source_id:
            continue
        try:
            info = client.fetch_info(source_id)
            link = info.get("source_url") or ""
            if not link:
                raise registry.ResolveError("unsupported-form", "task has no source_url")

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
            # 定时采集无人值守：解析成功即视为确认（人工链接流程才等用户 PATCH confirmed）。
            client.patch(source_id, status="confirmed")
            upload_process(client, source_id, chat_id)
            ok += 1
        except Exception as e:  # noqa: BLE001 - 单条隔离
            failed.append({"source_id": source_id, "error": str(e)[:200]})
            try:
                client.patch(source_id, status="error", error_code="collect_failed", error_message=str(e)[:300])
            except Exception:
                pass
    return {"ok": ok, "failed": failed}


def main() -> int:
    raw = os.environ.get("SHARD", "[]")
    try:
        shard = json.loads(raw)
    except json.JSONDecodeError:
        print("SHARD is not valid JSON", file=sys.stderr)
        return 2
    if "--dry-run" in sys.argv:
        api.env_or_die(["API_BASE", "TROVE_API_KEY"])
        print(json.dumps({"ok": 0, "failed": [], "dry_run": True}, ensure_ascii=False))
        return 0
    api.env_or_die(["API_BASE", "TROVE_API_KEY", "TG_API_ID", "TG_API_HASH", "TG_BOT_TOKEN", "TG_CHANNEL", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"])
    client = api.TroveApi(os.environ["API_BASE"], os.environ["TROVE_API_KEY"])
    result = run_shard(client, shard, os.environ["TG_CHANNEL"])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
