"""collect91 分片逻辑契约测试（§5.6：片大小 M=12，上限 6 片）。"""
from workers.scheduled.collect91.shards import MAX_SHARDS, shard_items


def test_shard_basic_12_per_shard():
    items = [f"91porn:viewkey={i:05d}" for i in range(25)]
    shards = shard_items(items)
    assert len(shards) == 3
    assert all(len(s["items"]) == 12 for s in shards[:2])
    assert len(shards[2]["items"]) == 1
    assert shards[0]["items"][0] == "91porn:viewkey=00000"
    assert [s["slot"] for s in shards] == [0, 1, 2]


def test_shard_empty():
    assert shard_items([]) == []


def test_shard_exact_multiple_72_gives_6_slots():
    items = list(range(72))
    shards = shard_items(items)
    assert len(shards) == 6
    assert [s["slot"] for s in shards] == list(range(6))
    assert all(len(s["items"]) == 12 for s in shards)


def test_shard_73_drops_overflow_but_keeps_6_shards():
    items = list(range(73))
    shards = shard_items(items)
    assert len(shards) == MAX_SHARDS == 6
    assert sum(len(s["items"]) for s in shards) == 72


def test_shard_custom_slots_cycles():
    shards = shard_items(list(range(24)), slots=[0, 3])
    assert [s["slot"] for s in shards] == [0, 3]


def test_shard_preserves_order():
    items = [str(i) for i in range(30)]
    shards = shard_items(items)
    assert shards[1]["items"][0] == "12"
