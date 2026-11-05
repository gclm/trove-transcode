import base64
import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
site = importlib.import_module("packages.sites.91porn")


def _encode(value: str, key: str) -> str:
    kb = key.encode()
    raw = bytes(b ^ kb[i % len(kb)] for i, b in enumerate(value.encode()))
    return base64.b64encode(raw).decode()


def _page(title: str, key: str) -> str:
    return (
        f'<i id="x1" data-ba760="{_encode(title, key)}" '
        f'data-ba760u="{_encode("uploader", key)}" data-ba760d="{_encode("469", key)}" style="display:none"></i>'
        f'<script>var m=0,id="x1",at="data-ba760",k="{key}";</script>'
    )


def test_decode_hidden_title_with_zero_width_chars():
    title = "soul\u200b认识\u2060很有偷情经验的少妇喜欢腹肌男"
    html = _page(title, "abcd1234")
    assert site.decode_page_title(html) == "soul认识很有偷情经验的少妇喜欢腹肌男"


def test_decode_hidden_title_ignores_anti_bot_title():
    html = '<title>请到91P945.com 观看 - 91porn</title>' + _page("真实标题", "k9x2")
    assert site.decode_page_title(html) == "真实标题"
