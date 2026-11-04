"""MTProto 上传必须真的 await（Telethon 是 async API）。

回归：直接调用 client.send_file() 拿到的是 coroutine，`msg.id` 会抛
AttributeError: 'coroutine' object has no attribute 'id'。
用假 telethon 模块验证整条路径在事件循环里跑通且返回 int。
"""
from __future__ import annotations

import sys
import types

import pytest

from lib import tg


class _Msg:
    id = 4242


class _FakeClient:
    def __init__(self, *a, **kw):
        self.parse_mode = None

    async def connect(self):
        return self

    async def disconnect(self):
        return None

    async def start(self, bot_token=None):
        assert bot_token, "bot_token must be passed explicitly"
        return self

    async def send_file(self, *a, **kw):
        return _Msg()


@pytest.fixture
def fake_telethon(monkeypatch, tmp_path):
    telethon = types.ModuleType("telethon")
    telethon.TelegramClient = _FakeClient
    sessions = types.ModuleType("telethon.sessions")
    sessions.StringSession = lambda s: s
    errors = types.ModuleType("telethon.errors")
    errors.FloodWaitError = type("FloodWaitError", (Exception,), {})
    tl = types.ModuleType("telethon.tl")
    tlt = types.ModuleType("telethon.tl.types")
    tlt.DocumentAttributeFilename = lambda file_name=None: ("attr", file_name)
    telethon.sessions, telethon.errors, telethon.tl = sessions, errors, tl
    tl.types = tlt
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", sessions)
    monkeypatch.setitem(sys.modules, "telethon.errors", errors)
    monkeypatch.setitem(sys.modules, "telethon.tl", tl)
    monkeypatch.setitem(sys.modules, "telethon.tl.types", tlt)
    monkeypatch.setenv("TG_API_ID", "1")
    monkeypatch.setenv("TG_API_HASH", "h")
    monkeypatch.setenv("BOTS", '[{"token": "111111:' + "A" * 35 + '", "bot_name": "main", "master": true}]')
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")
    return str(f)


def test_mtproto_upload_awaits_and_returns_message_id(fake_telethon):
    assert tg.mtproto_upload(fake_telethon, "-100123") == 4242


def test_chat_ref_numeric_string_becomes_int():
    # bot 不能把数字串当频道：必须转 int，否则 Telethon 按手机号解析报错。
    assert tg.chat_ref("-1001234567890") == -1001234567890
    assert tg.chat_ref("123456") == 123456


def test_chat_ref_username_and_private_link():
    assert tg.chat_ref("@my_channel") == "@my_channel"
    assert tg.chat_ref("https://t.me/c/1234567890/42") == -1001234567890
