"""resolve_with_retry：站点 502/配额页时退避重试，硬错误不重试。"""
from __future__ import annotations

import pytest

from lib import resolve
from packages.sites import registry


def test_retries_blocked_then_succeeds(monkeypatch):
    calls = {"n": 0}
    slept: list[int] = []

    def fake_resolve(url, cookie=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise registry.ResolveError("blocked", "91porn returned HTTP 502")
        return {"title": "ok"}

    monkeypatch.setattr(resolve, "resolve", fake_resolve)
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
    assert resolve.resolve_with_retry("https://x/view_video.php?viewkey=1") == {"title": "ok"}
    assert calls["n"] == 3
    assert slept == [20, 60]


def test_hard_error_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_resolve(url, cookie=None):
        calls["n"] += 1
        raise registry.ResolveError("not-found", "video deleted")

    monkeypatch.setattr(resolve, "resolve", fake_resolve)
    with pytest.raises(registry.ResolveError):
        resolve.resolve_with_retry("https://x/view_video.php?viewkey=1")
    assert calls["n"] == 1


def test_gives_up_after_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_resolve(url, cookie=None):
        calls["n"] += 1
        raise registry.ResolveError("blocked", "502")

    monkeypatch.setattr(resolve, "resolve", fake_resolve)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(registry.ResolveError):
        resolve.resolve_with_retry("https://x/view_video.php?viewkey=1")
    assert calls["n"] == 3
