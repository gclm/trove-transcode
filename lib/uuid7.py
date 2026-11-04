"""UUIDv7（时间有序）生成器。

主仓用 uuidv7 作文件 id（按时间有序、利于 B-tree 局部性）。Python 3.13 还没有
`uuid.uuid7`，这里按 RFC 9562 手写：48 位毫秒时间戳 + 版本/变体位 + 随机尾部。
"""
from __future__ import annotations

import os
import time
import uuid


def uuid7() -> str:
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    # 48 位时间戳
    b = bytearray(ms.to_bytes(6, "big") + rand)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # variant RFC 4122
    return str(uuid.UUID(bytes=bytes(b)))
