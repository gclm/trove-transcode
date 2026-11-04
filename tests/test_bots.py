"""BOTS JSON roster parsing and slot resolution."""
from __future__ import annotations

import json

import pytest

from lib import bots

TOKEN_A = "111111:" + "A" * 35
TOKEN_B = "654321:" + "B" * 35


def test_first_item_is_master_and_slots_follow_array_order():
    env = {"BOTS": json.dumps([
        {"token": TOKEN_A, "bot_name": "main", "master": False},
        {"token": TOKEN_B, "bot_name": "helper", "master": True},
    ])}
    roster = bots.load_bots(env)
    assert [b["bot_name"] for b in roster] == ["main", "helper"]
    assert [b["master"] for b in roster] == [True, False]
    assert bots.master_token(env) == TOKEN_A
    assert bots.token_for_slot(1, env) == TOKEN_B


def test_invalid_json_and_duplicate_ids_fail():
    with pytest.raises(bots.BotConfigError, match="valid JSON"):
        bots.load_bots({"BOTS": "{"})
    with pytest.raises(bots.BotConfigError, match="duplicate"):
        bots.load_bots({"BOTS": json.dumps([
            {"token": TOKEN_A, "bot_name": "a"},
            {"token": TOKEN_A, "bot_name": "b"},
        ])})


def test_missing_slot_does_not_fall_back_to_master():
    with pytest.raises(bots.BotConfigError, match="slot 1"):
        bots.token_for_slot(1, {"BOTS": json.dumps([{"token": TOKEN_A, "bot_name": "main"}])})
