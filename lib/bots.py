"""Bot roster loaded from the BOTS environment variable.

BOTS is a JSON array. Each item:
    {"token": "123456:ABC...", "bot_name": "gclm_bot", "master": true}

The first item is always the master bot. The array order is also the HLS shard
slot order: slot 0 is the master, slot n is the n-th item. A slot token must
never silently fall back to another bot: a Telegram file_id is bound to the bot
that uploaded the file, so reading with the wrong bot returns FILE_ID_INVALID.

The runner fetches BOTS from the Worker at startup (``lib.config.bootstrap``);
there is no flat ``TG_BOT_TOKEN`` fallback.
"""
from __future__ import annotations

import json
import os
import re

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,60}$")


class BotConfigError(RuntimeError):
    pass


def _normalise(item: object, index: int) -> dict:
    if not isinstance(item, dict):
        raise BotConfigError(f"BOTS[{index}] must be an object")
    token = str(item.get("token") or "").strip()
    name = str(item.get("bot_name") or "").strip().lstrip("@")
    if not TOKEN_RE.match(token):
        raise BotConfigError(f"BOTS[{index}].token is invalid")
    if not name:
        raise BotConfigError(f"BOTS[{index}].bot_name is required")
    return {
        "token": token,
        "bot_name": name,
        # The first configured bot is always the master; the field is
        # accepted for readability but normalised from array order.
        "master": index == 0,
    }


def load_bots(env: dict[str, str] | None = None) -> list[dict]:
    """Parse and validate BOTS. Missing BOTS is a hard error."""
    source = os.environ if env is None else env
    raw = str(source.get("BOTS") or "").strip()
    if not raw:
        raise BotConfigError("BOTS is not configured")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BotConfigError(f"BOTS must be valid JSON: {e}") from e
    if not isinstance(parsed, list) or not parsed:
        raise BotConfigError("BOTS must be a non-empty JSON array")
    bots = [_normalise(item, i) for i, item in enumerate(parsed)]
    ids = [b["token"].split(":", 1)[0] for b in bots]
    if len(ids) != len(set(ids)):
        raise BotConfigError("BOTS contains duplicate bot ids")
    return bots


def master_token(env: dict[str, str] | None = None) -> str:
    return load_bots(env)[0]["token"]


def token_for_slot(slot: int, env: dict[str, str] | None = None) -> str:
    if not isinstance(slot, int) or slot < 0:
        raise BotConfigError(f"invalid bot slot {slot}")
    bots = load_bots(env)
    if slot >= len(bots):
        raise BotConfigError(f"bot slot {slot} is not configured (BOTS has {len(bots)} items)")
    return bots[slot]["token"]
