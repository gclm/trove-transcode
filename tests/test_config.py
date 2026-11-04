"""runner-config 到进程环境的映射契约。"""
import json
import os

import pytest

from lib import config


def test_apply_maps_telegram_and_bots(monkeypatch):
    for key in ("TG_API_ID", "TG_API_HASH", "TG_CHANNEL", "BOTS", "PORN91_COOKIE", "DOUYIN_COOKIE", "XHS_COOKIE", "HAIJIAO_COOKIE"):
        monkeypatch.delenv(key, raising=False)
    config.apply(
        {
            "tg_api_id": 1234567,
            "tg_api_hash": "0123456789abcdef0123456789abcdef",
            "tg_channel": "-100123456789",
            "bots": [
                {"token": "123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHI", "bot_name": "master_bot", "master": True}
            ],
            "cookies": {"PORN91_COOKIE": "guest-cookie", "DOUYIN_COOKIE": "", "XHS_COOKIE": None, "HAIJIAO_COOKIE": "x"},
        }
    )
    assert os.environ["TG_API_ID"] == "1234567"
    assert os.environ["TG_API_HASH"] == "0123456789abcdef0123456789abcdef"
    assert os.environ["TG_CHANNEL"] == "-100123456789"
    assert json.loads(os.environ["BOTS"])[0]["bot_name"] == "master_bot"
    assert os.environ["PORN91_COOKIE"] == "guest-cookie"
    assert "DOUYIN_COOKIE" not in os.environ
    assert "XHS_COOKIE" not in os.environ
    assert os.environ["HAIJIAO_COOKIE"] == "x"


def test_apply_rejects_incomplete_config(monkeypatch):
    for key in ("TG_API_ID", "TG_API_HASH", "TG_CHANNEL", "BOTS"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(config.RunnerConfigError, match="no bots"):
        config.apply({"tg_api_id": 1, "bots": []})
