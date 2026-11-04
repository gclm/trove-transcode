"""分片：把待采条目切成 matrix 子任务的片（plan §5.6）。

片大小 12：单 job 里每条要跑「解析→下载→转码→上传」，2 核 runner 上
12 条约 1 小时内完成；最多 6 片（6 个 bot 槽位），超出的条目留到下一轮 dedup 重查。
"""
from __future__ import annotations

PER_SHARD = 12
MAX_SHARDS = 6


def shard_items(items: list, per: int = PER_SHARD, max_shards: int = MAX_SHARDS, slots: list[int] | None = None) -> list[dict]:
    """按顺序切片，返回 [{slot, items}]；超出上限的部分丢弃（下轮重查）。

    slots 给定时按 shard 顺序循环分配 bot 槽位；缺省为 0..n-1（对应 6 个上传 bot）。
    当前未配置 helper bot 时 collect job 传 slots=[0]，所有片共用主 bot。
    """
    if not items:
        return []
    limit = max_shards * per
    kept = list(items)[:limit]
    chunks = [kept[i:i + per] for i in range(0, len(kept), per)]
    if slots is None:
        slots = list(range(len(chunks)))
    if not slots:
        slots = [0]
    return [{"slot": int(slots[i % len(slots)]), "items": chunk} for i, chunk in enumerate(chunks)]
