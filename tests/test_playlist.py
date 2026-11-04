"""§3.6 m3u8 playlist 格式契约测试（对齐 plan 20261103 §3.6）。"""
import math

from lib.transcode import build_playlist


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
    assert "/dl/seg/01JFILE/000.ts?fid=AAA1&sz=100" in lines
    assert "/dl/seg/01JFILE/001.ts?fid=BBB2&sz=101" in lines


def test_playlist_targetduration_is_ceiling():
    segs = [_seg(0, "F", 10, 5.2), _seg(1, "G", 10, 6.0)]
    text = build_playlist(segs, "X")
    assert "#EXT-X-TARGETDURATION:6" in text


def test_playlist_seq_is_three_digit_zero_padded():
    segs = [_seg(32, "F32", 1, 2.0)]
    text = build_playlist(segs, "X")
    assert "/dl/seg/X/032.ts?fid=F32&sz=1" in text


def test_playlist_absolute_path_reference():
    segs = [_seg(0, "F", 1, 2.0)]
    text = build_playlist(segs, "X")
    # 绝对路径引用，不依赖 m3u8 自身 URL 形状
    assert "/dl/seg/X/000.ts?fid=F&sz=1" in text
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
