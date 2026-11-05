"""采集分片流水线：一片内逐条走完「解析 → 确认 → 下载 → 转码 → 上传」。

片内串行（2 核 runner，ffmpeg 会把 CPU 吃满）；片间并行由 workflow matrix 提供。
单条失败只影响该条：记日志后继续，下一轮 dedup 会重新拾起未入库的条目。
"""
from __future__ import annotations

import json
import os
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from lib import api, config, resolve, bots  # noqa: E402
from packages.sites import registry  # noqa: E402
from workers.upload.job import process as upload_process  # noqa: E402


def _upload_token_for_slot(slot: int) -> str:
    """按 BOTS JSON 的数组顺序取槽位 token。未配置的槽位直接失败，不静默串号。"""
    try:
        return bots.token_for_slot(slot)
    except bots.BotConfigError as e:
        raise RuntimeError(str(e)) from e


def run_shard(client: api.TroveApi, shard: dict, chat_id: str, upload_token: str | None = None) -> dict:
    ok, failed = 0, []
    slot = int(shard.get("slot", 0))
    entries = shard.get("items") or []
    if upload_token is None:
        upload_token = _upload_token_for_slot(slot)
    for entry in entries:
        source_id = entry.get("source_id")
        if not source_id:
            continue
        try:
            info = client.fetch_info(source_id)
            link = info.get("source_url") or ""
            if not link:
                raise registry.ResolveError("unsupported-form", "task has no source_url")

            # 每条只解析一次：元数据同时用于状态回报与 upload_process 的下载直链。
            meta = resolve.resolve_with_retry(link)
            client.patch(
                source_id,
                status="confirmed",
                name=meta.get("title") or info.get("name"),
                duration_s=meta.get("duration_s"),
                poster_url=meta.get("poster_url"),
                external_key=meta.get("external_key") or entry.get("key"),
                site=meta.get("site") or resolve.site_of(link),
                author=meta.get("author"),
                author_url=meta.get("author_url"),
                published_at=meta.get("published_at"),
                cover_url=meta.get("cover_url"),
                source_url=meta.get("source_url") or link,
            )
            # 定时采集无人值守：解析成功即视为确认（人工链接流程才等用户 PATCH confirmed）。
            upload_process(client, source_id, chat_id, meta=meta, upload_token=upload_token, upload_slot=slot)
            ok += 1
        except Exception as e:  # noqa: BLE001 - 单条隔离
            failed.append({"source_id": source_id, "error": str(e)[:200]})
            try:
                client.patch(source_id, status="error", error_code="collect_failed", error_message=str(e)[:300])
            except Exception:
                pass
        # 片内节流：72 条/天的大批量下，给 Telegram 留出喘息窗口，避免 FloodWait。
        time.sleep(5)
    return {"ok": ok, "failed": failed}


def main() -> int:
    raw = os.environ.get("SHARD", "[]")
    try:
        shard = json.loads(raw)
    except json.JSONDecodeError:
        print("SHARD is not valid JSON", file=sys.stderr)
        return 2
    if isinstance(shard, list):  # 兼容旧 workflow 的裸数组 SHARD
        shard = {"slot": 0, "items": shard}
    if "--dry-run" in sys.argv:
        api.env_or_die(["API_BASE", "TROVE_API_KEY"])
        print(json.dumps({"ok": 0, "failed": [], "dry_run": True}, ensure_ascii=False))
        return 0
    api.env_or_die(["API_BASE", "TROVE_API_KEY"])
    client = api.TroveApi(os.environ["API_BASE"], os.environ["TROVE_API_KEY"])
    config.bootstrap(client)
    result = run_shard(client, shard, os.environ["TG_CHANNEL"])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
