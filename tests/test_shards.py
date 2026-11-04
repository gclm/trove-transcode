"""collect91 分片逻辑契约测试（§5.6：片大小 M=12，上限 256 片）。"""
from workers.scheduled.collect91.shards import shard_items


def test_shard_basic_12_per_shard():
    items = [f"91porn:viewkey={i:05d}" for i in range(25)]
    shards = shard_items(items)
    assert len(shards) == 3
    assert all(len(s) == 12 for s in shards[:2])
    assert len(shards[2]) == 1
    assert shards[0][0] == "91porn:viewkey=00000"


def test_shard_empty():
    assert shard_items([]) == []


def test_shard_exact_multiple():
    items = list(range(24))
    shards = shard_items(items)
    assert len(shards) == 2
    assert all(len(s) == 12 for s in shards)


def test_shard_capped_at_256_shards():
    items = [f"ek{i}" for i in range(3100)]  # 3100/12 = 259 片 > 256
    shards = shard_items(items)
    assert len(shards) == 256
    covered = sum(len(s) for s in shards)
    assert covered == 256 * 12  # 超出部分丢弃，留待下一轮 dedup 重查


def test_shard_preserves_order():
    items = [str(i) for i in range(30)]
    shards = shard_items(items)
    assert shards[1][0] == "12"
