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


def test_parse_page_extracts_author_date_cover_and_source_url():
    html = (
        '<html><head><title>测试标题 - 91porn</title></head><body>'
        '<video poster="https://cdn.example/cover.jpg"></video>'
        '<span class="info">Added: </span><span class="title-yakov">2026-11-03</span>'
        '<span class="info">From: </span><span class="title-yakov">'
        '<a href="https://903.9p234.com/uprofile.php?UID=abc"><span class="title">作者甲</span></a>'
        '</span>'
        '<script>strencode2("abc");</script>'
        '<script>var player_one = 1;</script>'
        '<div id="v_desc">desc</div>'
        '</body></html>'
    )
    # 用真实可解析的 strencode2 片段替换占位，避免 parse_page 判为无播放器。
    html = html.replace('strencode2("abc");', 'strencode2("%3Csource%20src%3D%27https%3A%2F%2Fcdn.example%2Fv.mp4%27%3E");')
    meta = site.parse_page(html, "https://903.9p234.com/view_video.php?viewkey=abc")
    assert meta["title"] == "测试标题"
    assert meta["author"] == "作者甲"
    assert meta["author_url"].endswith("uprofile.php?UID=abc")
    assert meta["published_at"] == "2026-11-03T00:00:00"
    assert meta["cover_url"] == "https://cdn.example/cover.jpg"
    assert meta["source_url"].endswith("viewkey=abc")
