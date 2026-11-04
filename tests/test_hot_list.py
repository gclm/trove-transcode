"""91porn 热点列表：hot1/2/3 三页去重，每页 24 个唯一 viewkey。"""
import importlib

import pytest

_91 = importlib.import_module("packages.sites.91porn")


def test_extract_viewkeys_dedupes_and_orders():
    html = (
        "<a href='view_video.php?viewkey=aaa'>1</a>"
        "<a href='view_video.php?viewkey=aaa'>dup</a>"
        "<a href='view_video.php?viewkey=bbb'>2</a>"
        "<div>ad</div>"
    )
    assert _91._extract_viewkeys(html) == ["91porn:viewkey=aaa", "91porn:viewkey=bbb"]


def test_list_hot_walks_three_pages(monkeypatch):
    calls = []

    async def fake(page, cookie):
        calls.append(page)
        return "".join(f"<a href='view_video.php?viewkey=p{page}x{i:02d}'>x</a>" for i in range(24))

    monkeypatch.setattr(_91, "_fetch_hot_page", fake)
    keys = _91.list_hot(limit=72, pages=3)
    assert calls == [1, 2, 3]
    assert len(keys) == 72
    assert keys[0] == "91porn:viewkey=p1x00"
    assert keys[-1] == "91porn:viewkey=p3x23"


def test_list_hot_respects_limit(monkeypatch):
    async def fake(page, cookie):
        return "".join(f"<a href='view_video.php?viewkey=p{page}x{i:02d}'>x</a>" for i in range(24))

    monkeypatch.setattr(_91, "_fetch_hot_page", fake)
    assert len(_91.list_hot(limit=30, pages=3)) == 30


def test_list_hot_raises_when_empty(monkeypatch):
    async def fake(page, cookie):
        return "<html>no videos</html>"

    monkeypatch.setattr(_91, "_fetch_hot_page", fake)
    with pytest.raises(Exception) as ei:
        _91.list_hot(limit=72, pages=3)
    assert getattr(ei.value, "code", "") == "blocked"
