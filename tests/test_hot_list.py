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


def test_base_candidates_override_first_and_dedupes(monkeypatch):
    monkeypatch.setenv("SITE_91PORN_BASE", "https://903.9p234.com")
    bases = _91.base_candidates()
    assert bases[0] == "https://903.9p234.com"
    assert len(bases) == len(set(bases))


def test_list_category_dedupes_across_pages(monkeypatch):
    calls = []

    async def fake(base, category, page, cookie):
        calls.append((base, category, page))
        return (
            "<a href='view_video.php?viewkey=aaa'>1</a>"
            "<a href='view_video.php?viewkey=aaa'>dup</a>"
            f"<a href='view_video.php?viewkey=p{page}'>2</a>"
        )

    monkeypatch.setattr(_91, "_fetch_list_page", fake)
    keys = _91.list_category("https://91porn.com", category="tf", limit=10, pages=3)
    assert calls == [
        ("https://91porn.com", "tf", 1),
        ("https://91porn.com", "tf", 2),
        ("https://91porn.com", "tf", 3),
    ]
    assert keys == ["91porn:viewkey=aaa", "91porn:viewkey=p1", "91porn:viewkey=p2", "91porn:viewkey=p3"]


def test_pick_base_falls_back_to_mirror(monkeypatch):
    import types
    import lib.web

    async def fake_fetch(url, **kwargs):
        if "903.9p234.com" in url:
            return types.SimpleNamespace(status_code=200, text="<a href='view_video.php?viewkey=ok'>x</a>")
        return types.SimpleNamespace(status_code=403, text="blocked")

    monkeypatch.setattr(lib.web, "fetch_page", fake_fetch)
    assert _91.pick_base_sync(category="tf") == "https://903.9p234.com"
