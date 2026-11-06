"""§3.6 m3u8 playlist 格式契约测试（对齐 plan 20261103 §3.6）。"""
import math

from lib.transcode import build_playlist, segment_rows


def _seg(seq, fid, size, dur):
    return {"seq": seq, "tg_file_id": fid, "size": size, "duration": dur}


def test_playlist_matches_design_sample():
    segs = [_seg(0, "AAA1", 100, 6.006), _seg(1, "BBB2", 101, 5.0)]
    text = build_playlist(segs, "01JFILE")
    lines = text.strip().split("\n")
    assert lines[0] == "#EXTM3U"
    assert "#EXT-X-VERSION:6" in lines
    assert "#EXT-X-MEDIA-SEQUENCE:0" in lines
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in lines
    assert "#EXT-X-ENDLIST" == lines[-1]
    # TARGETDURATION = ceil(max duration) = ceil(6.006) = 7
    assert "#EXT-X-TARGETDURATION:7" in lines
    # 每分片两行：EXTINF + 内嵌引用；seq 三位零填充
    assert "#EXTINF:6.006," in lines
    assert "000.ts" in lines
    assert "001.ts" in lines


def test_playlist_targetduration_is_ceiling():
    segs = [_seg(0, "F", 10, 5.2), _seg(1, "G", 10, 6.0)]
    text = build_playlist(segs, "X")
    assert "#EXT-X-TARGETDURATION:6" in text


def test_playlist_seq_is_three_digit_zero_padded():
    segs = [_seg(32, "F32", 1, 2.0)]
    text = build_playlist(segs, "X")
    assert "032.ts" in text


def test_playlist_relative_path_reference():
    segs = [_seg(0, "F", 1, 2.0)]
    text = build_playlist(segs, "X")
    # PLAN-012: 相对路径，由 /hls/<id>/index.m3u8 所在目录解析
    assert "000.ts" in text
    assert "/dl/seg/" not in text
    assert "http" not in text


def test_playlist_extinf_keeps_precision():
    segs = [_seg(0, "F", 1, 6.006001)]
    text = build_playlist(segs, "X")
    assert "#EXTINF:6.006001," in text


def test_playlist_empty_rejected():
    try:
        build_playlist([], "X")
    except ValueError:
        return
    raise AssertionError("empty segments must be rejected")


def test_playlist_omits_bot_slot_for_main_bot():
    segs = [{"seq": 0, "tg_file_id": "F", "size": 1, "duration": 2.0, "bot_slot": 0}]
    assert "&b=" not in build_playlist(segs, "X")


def test_playlist_has_no_query_for_helper_bot():
    segs = [{"seq": 0, "tg_file_id": "F", "size": 1, "duration": 2.0, "bot_slot": 3}]
    text = build_playlist(segs, "X")
    assert "000.ts" in text
    assert "?fid=" not in text


def test_segment_rows_keeps_bot_slot():
    rows = segment_rows([{"tg_file_id": "F", "size": 2, "duration": 3.0}], "X", bot_slot=4)
    assert rows[0]["bot_slot"] == 4


def test_playlist_has_no_mid_query():
    segs = [{"seq": 0, "tg_file_id": "F", "size": 1, "duration": 2.0, "bot_slot": 3, "mid": 5501}]
    text = build_playlist(segs, "X")
    assert "000.ts" in text
    assert "mid=" not in text


def test_segment_rows_keeps_mid():
    rows = segment_rows([{"tg_file_id": "F", "size": 2, "duration": 3.0, "mid": 5502}], "X")
    assert rows[0]["mid"] == 5502
