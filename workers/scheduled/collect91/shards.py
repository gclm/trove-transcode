"""分片：把待采条目切成 matrix 子任务的片（plan §5.6）。

片大小 12：单 job 里每条要跑「解析→下载→转码→上传」，2 核 runner 上
12 条正好压满 6 小时上限；片数上限 256，超出的条目留到下一轮 dedup 重查。
"""
from __future__ import annotations

PER_SHARD = 12
MAX_SHARDS = 256


def shard_items(items: list, per: int = PER_SHARD, max_shards: int = MAX_SHARDS) -> list[list]:
    """按顺序切片，保留原顺序；超出上限的部分丢弃（下轮重查）。"""
    if not items:
        return []
    limit = max_shards * per
    kept = list(items)[:limit]
    return [kept[i:i + per] for i in range(0, len(kept), per)]
