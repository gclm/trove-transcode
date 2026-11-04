"""站点 extractor 契约测试（91porn strencode2 / douyin feed JSON / xiaohongshu / haijiao / registry）。

91porn 与 douyin 逻辑移植自旧真源（scripts/lib/resolvers.mjs + trove 主仓 server/resolvers/*.ts，
主仓仅供移植参考、不改动）。xiaohongshu / haijiao 无既有实现，为设计新增，待真机 fixture 校准。
"""
import importlib
import json

import pytest

from packages.sites import registry

_91 = importlib.import_module("packages.sites.91porn")
douyin = importlib.import_module("packages.sites.douyin")
xhs = importlib.import_module("packages.sites.xiaohongshu")
haijiao = importlib.import_module("packages.sites.haijiao")


# ---------- 91porn ----------

def _page(html):
    return html


STRENCODE_SRC = (
    "https://cdn.example.com/hls/v.mp4?secure=b64zzz,1700000000"
)

def test_91porn_strencode2_decode():
    from urllib.parse import quote
    cipher = quote(f"<source src='{STRENCODE_SRC}'>", safe="")
    html = (
        "<html><head><title>测试标题 - 91porn</title></head><body>"
        f"<script>document.write(strencode2(\"{cipher}\"));</script>"
        "<video poster=\"https://img.example.com/p.jpg\">"
        "<span class=\"video-info-span\">00:15:24</span>"
        "</body></html>"
    )
    r = _91.parse_page(html, "https://www.91porn.com/view_video.php?viewkey=fb57545243e76b64dd2c")
    assert r["title"] == "测试标题"
    assert r["duration_s"] == 15 * 60 + 24
    assert r["download_url"] == STRENCODE_SRC
    assert r["external_key"] == "91porn:viewkey=fb57545243e76b64dd2c"
    assert r["poster_url"] == "https://img.example.com/p.jpg"
    assert r["download_headers"]["Referer"] == "https://91porn.com/"


def test_91porn_anti_bot_blocked():
    html = "<html><body>temporarily unable to process</body></html>"
    with pytest.raises(Exception) as ei:
        _91.parse_page(html, "https://www.91porn.com/view_video.php?viewkey=x")
    assert getattr(ei.value, "code", "") == "blocked"


def test_91porn_quota():
    html = "<html><body>每天观看次数已达上限</body></html>"
    with pytest.raises(Exception) as ei:
        _91.parse_page(html, "https://www.91porn.com/view_video.php?viewkey=x")
    assert getattr(ei.value, "code", "") == "quota"


def test_91porn_entry_from_key_roundtrip():
    url = _91.entry_from_key("91porn:viewkey=fb57545243e76b64dd2c")
    assert url == "https://www.91porn.com/view_video.php?viewkey=fb57545243e76b64dd2c"


# ---------- douyin ----------

FEED = {
    "aweme_list": [
        {
            "desc": "某抖音视频" * 100,  # >300 chars
            "author": {"nickname": "作者甲"},
            "video": {
                "duration": 15300,
                "play_addr": {"uri": "v0300fg10000", "url_list": ["https://cdn.douyin.com/a.mp4", "https://x.amemv.com/b.mp4"]},
                "cover": {"url_list": ["https://p.douyinpic.com/c.jpeg"]},
            },
        }
    ]
}


def test_douyin_parse_feed():
    r = douyin.parse_feed(FEED, "7123456789012345678")
    assert r["title"] == FEED["aweme_list"][0]["desc"][:300]
    assert r["duration_s"] == 15  # 15300ms → round(15.3) = 15
    assert r["poster_url"] == "https://p.douyinpic.com/c.jpeg"
    assert r["external_key"] == "douyin:video_id=7123456789012345678"
    # aggregate 端点优先（re-302 出新 CDN 链接，寿命长于签名 CDN 直链）
    assert r["download_url"] == "https://api-play.amemv.com/aweme/v1/play/?video_id=v0300fg10000"
    assert "User-Agent" in r["download_headers"]


def test_douyin_parse_feed_rejects_images():
    feed = json.loads(json.dumps(FEED))
    feed["aweme_list"][0]["images"] = [{"url_list": ["x"]}]
    with pytest.raises(Exception) as ei:
        douyin.parse_feed(feed, "712")
    assert getattr(ei.value, "code", "") == "unsupported-form"


def test_douyin_parse_feed_rejects_live():
    feed = {"aweme_list": [{"desc": "直播", "video": {}}]}
    with pytest.raises(Exception):
        douyin.parse_feed(feed, "712")


def test_douyin_aweme_id_from_url():
    assert douyin.extract_aweme_id("https://www.douyin.com/video/7123456789012345678?x=1") == "7123456789012345678"
    assert douyin.extract_aweme_id("https://v.douyin.com/iAbCdE/") is None


def test_douyin_ytdlp_fallback_chain():
    # yt-dlp 优先；失败回落自写 feed 解析。模块必须暴露两条路径。
    assert callable(douyin.resolve_via_ytdlp)
    assert callable(douyin.resolve_via_feed)


# ---------- xiaohongshu ----------

def test_xhs_note_id():
    assert xhs.extract_note_id("https://www.xiaohongshu.com/explore/65aa1b12000000001e023a2c?x=1") == "65aa1b12000000001e023a2c"
    assert xhs.extract_note_id("https://xhslink.com/abc") is None


def test_xhs_parse_initial_state():
    state = {
        "noteData": {
            "data": {
                "title": "笔记标题",
                "id": "65aa1b12000000001e023a2c",
                "media": {
                    "videoMedia": {
                        "stream": {
                            "h264": [
                                {"master_url": "https://sns-video.example.com/v.mp4", "video_url": None, "duration": 61},
                            ]
                        }
                    },
                    "image": {"urlDefault": "https://sns-img.example.com/p.jpg"},
                },
            }
        }
    }
    html = f"<html><script>window.__INITIAL_STATE__={json.dumps(state)}</script></html>"
    r = xhs.parse_page(html, "https://www.xiaohongshu.com/explore/65aa1b12000000001e023a2c")
    assert r["title"] == "笔记标题"
    assert r["download_url"] == "https://sns-video.example.com/v.mp4"
    assert r["duration_s"] == 61
    assert r["poster_url"] == "https://sns-img.example.com/p.jpg"
    assert r["external_key"] == "xiaohongshu:note_id=65aa1b12000000001e023a2c"


# ---------- haijiao ----------

def test_haijiao_post_id():
    assert haijiao.extract_post_id("https://hjd2048.com/2048/thread-1234567-1-1.html") == "1234567"
    assert haijiao.extract_post_id("https://example.com/nope") is None


def test_haijiao_parse_video_link():
    html = (
        "<html><body>"
        "<video><source src=\"https://cdn.haijiao.example/hls/index.m3u8\" type=\"application/x-mpegURL\"></video>"
        "</body></html>"
    )
    r = haijiao.parse_page(html, "https://hjd2048.com/2048/thread-1234567-1-1.html")
    assert r["download_url"] == "https://cdn.haijiao.example/hls/index.m3u8"
    assert r["external_key"] == "haijiao:post_id=1234567"
    assert r["download_headers"]  # 带 Referer 防盗链


def test_haijiao_parse_mp4_link():
    html = "<html><body><a href=\"https://cdn.example/v.mp4\">下载</a></body></html>"
    r = haijiao.parse_page(html, "https://hjd2048.com/2048/thread-99-1-1.html")
    assert r["download_url"] == "https://cdn.example/v.mp4"
    assert r["external_key"] == "haijiao:post_id=99"


# ---------- registry ----------

def test_registry_matches_by_url():
    assert registry.match_url("https://www.91porn.com/view_video.php?viewkey=abc") is not None
    assert registry.match_url("https://v.douyin.com/iAbCdE/") is not None
    assert registry.match_url("https://www.douyin.com/video/7123456789012345678") is not None
    assert registry.match_url("https://www.xiaohongshu.com/explore/65aa1b12000000001e023a2c") is not None
    assert registry.match_url("https://hjd2048.com/2048/thread-1234567-1-1.html") is not None
    assert registry.match_url("https://example.com/x") is None


def test_registry_entry_from_key():
    assert registry.entry_from_key("douyin:video_id=712") == "https://www.douyin.com/video/712"
    assert registry.entry_from_key("xiaohongshu:note_id=65aa") == "https://www.xiaohongshu.com/explore/65aa"
    assert registry.entry_from_key("haijiao:post_id=123") == "https://hjd2048.com/2048/thread-123-1-1.html"


def test_registry_sites_cover_four():
    assert set(registry.SITES.keys()) == {"91porn", "douyin", "xiaohongshu", "haijiao"}
