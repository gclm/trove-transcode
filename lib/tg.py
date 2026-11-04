"""Telegram 两条上传/下载通道（plan §3.4）。

| 载体 | 通道 | 适用 | 回执 |
|---|---|---|---|
| 原档大文件 | MTProto（Telethon，bot 账号） | 视频源、>20MB 非视频 | message_id → tg_mid |
| 分片 / playlist / 封面 | Bot API（multipart） | 每个文件 ≤50MB | file_id → 播放直取 |

分片必须走 Bot API：只有 Bot API 会给出可复用的 `file_id`，MTProto 的
message_id 无法用于 getFile（plan §3.4 关键坑）。
"""
from __future__ import annotations

import asyncio
import re

import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid

API_ROOT = "https://api.telegram.org"
MEDIA_GROUP_MAX = 10


class TgError(RuntimeError):
    def __init__(self, message: str, code: int = 0, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def _token(token: str | None = None) -> str:
    tok = token or os.environ.get("TG_BOT_TOKEN", "")
    if not tok:
        raise TgError("TG_BOT_TOKEN missing")
    return tok


def _multipart(fields: dict, files: list[tuple[str, str, str]]) -> tuple[bytes, str]:
    """手写 multipart：避免给 runner 增加 requests 依赖。"""
    boundary = f"----trove{uuid.uuid4().hex}"
    out = bytearray()
    for key, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
    for field, path, filename in files:
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        out += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        with open(path, "rb") as fh:
            out += fh.read()
        out += b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def bot_call(method: str, fields: dict | None = None, files: list[tuple[str, str, str]] | None = None, token: str | None = None) -> dict:
    """调用 Bot API，返回 result；429 自动按 retry_after 重试一次。"""
    url = f"{API_ROOT}/bot{_token(token)}/{method}"
    if files:
        body, ctype = _multipart(fields or {}, files)
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
    else:
        payload = json.dumps(fields or {}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            err = json.loads(raw.decode())
        except Exception:
            raise TgError(f"{method} failed: {e.code}") from None
        if e.code == 429:
            retry_after = int((err.get("parameters") or {}).get("retry_after") or 5)
            time.sleep(min(retry_after, 60))
            return bot_call(method, fields, files, token)
        raise TgError(f"{method} failed: {err.get('description') or e.code}", code=e.code) from None
    if not data.get("ok"):
        raise TgError(f"{method} failed: {data.get('description')}")
    return data["result"]


def bot_send_document(path: str, chat_id: str, filename: str | None = None, caption: str | None = None, token: str | None = None) -> dict:
    """≤20MB 非视频 / playlist / 任意文档 → {message_id, file_id}。"""
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption
    res = bot_call("sendDocument", fields, [("document", path, filename or os.path.basename(path))], token)
    doc = res.get("document") or {}
    return {"message_id": res.get("message_id"), "file_id": doc.get("file_id")}


def bot_send_photo(path: str, chat_id: str, caption: str | None = None, token: str | None = None) -> dict:
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption
    res = bot_call("sendPhoto", fields, [("photo", path, os.path.basename(path))], token)
    photos = res.get("photo") or []
    best = photos[-1] if photos else {}
    return {"message_id": res.get("message_id"), "file_id": best.get("file_id")}


def bot_send_media_group(paths: list[str], chat_id: str, token: str | None = None) -> list[dict]:
    """分片批量上传：每批 ≤10 个文件，回执逐个给出 file_id。

    Bot API 的 sendMediaGroup 每个 item 用 attach://fN 引用本批 multipart 里的文件。
    """
    out: list[dict] = []
    for start in range(0, len(paths), MEDIA_GROUP_MAX):
        batch = paths[start:start + MEDIA_GROUP_MAX]
        media = [{"type": "document", "media": f"attach://f{i}"} for i in range(len(batch))]
        files = [(f"f{i}", p, os.path.basename(p)) for i, p in enumerate(batch)]
        res = bot_call("sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)}, files, token)
        for msg in res:
            doc = msg.get("document") or {}
            out.append({"message_id": msg.get("message_id"), "file_id": doc.get("file_id")})
    return out


def bot_get_file_bytes(file_id: str, token: str | None = None) -> bytes:
    info = bot_call("getFile", {"file_id": file_id}, None, token)
    path = info.get("file_path")
    if not path:
        raise TgError("getFile returned no file_path")
    url = f"{API_ROOT}/file/bot{_token(token)}/{path}"
    with urllib.request.urlopen(url, timeout=300) as resp:
        return resp.read()


def bot_file_size(file_id: str, token: str | None = None) -> int:
    info = bot_call("getFile", {"file_id": file_id}, None, token)
    return int(info.get("file_size") or 0)


# ---- MTProto（Telethon，懒加载） -------------------------------------------------


def chat_ref(chat_id: str | int) -> int | str:
    """频道引用归一：数字串转 int（bot 不能用字符串查实体），@name 原样。

    Telethon 把纯数字字符串当手机号解析，bot 账号会直接报
    `Cannot get entity by phone number as a bot`。
    """
    s = str(chat_id).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    m = re.match(r"^(?:https?://)?t\.me/c/(\d+)", s)
    if m:
        return int("-100" + m.group(1))
    return s


def mtproto_upload(path: str, chat_id: str, caption: str | None = None, attempts: int = 4, filename: str | None = None) -> int:
    """用 bot 账号把文件传进频道，返回 message_id（→ tg_mid）。

    Telethon 是 async API：整段跑在一个事件循环里（`asyncio.run`），
    调用方（同步的 job 代码）拿到的仍是普通 int。
    Telethon 只在真正需要时 import：解析/编排 job 不需要它。
    `filename` 用于覆盖 TG 文档名（默认取临时文件名，会让频道里显示 source.mp4）。
    """
    return asyncio.run(_mtproto_upload(path, chat_id, caption, attempts, filename))


async def _mtproto_upload(path: str, chat_id: str, caption: str | None, attempts: int, filename: str | None) -> int:
    from telethon import TelegramClient  # noqa: PLC0415
    from telethon.errors import FloodWaitError  # noqa: PLC0415
    from telethon.sessions import StringSession  # noqa: PLC0415
    from telethon.tl.types import DocumentAttributeFilename  # noqa: PLC0415

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = os.environ.get("TG_SESSION") or ""
    name = os.path.basename(filename) if filename else os.path.basename(path)

    for attempt in range(attempts):
        try:
            # 不能用 `async with`：TelegramClient.__aenter__ 会调 start() 且不带
            # bot_token，非交互环境直接 input() → EOFError。显式 connect/start。
            client = TelegramClient(StringSession(session), api_id, api_hash)
            await client.connect()
            client.parse_mode = None
            try:
                await client.start(bot_token=_token())
                msg = await client.send_file(
                    chat_ref(chat_id),
                    path,
                    caption=caption,
                    force_document=True,
                    attributes=[DocumentAttributeFilename(file_name=name)],
                )
                return int(msg.id)
            finally:
                await client.disconnect()
        except FloodWaitError as e:  # 频道限流：等满再试
            if attempt == attempts - 1:
                raise TgError(f"flood wait {e.seconds}s", code=429, retry_after=e.seconds) from None
            await asyncio.sleep(min(int(e.seconds) + 1, 120))
    raise TgError("mtproto upload failed")
