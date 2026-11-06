"""ffmpeg/ffprobe 原语参数契约测试——参数对齐旧 pipeline.mjs。"""
from lib.transcode import (
    SEGMENT_SECONDS_MAX,
    SEGMENT_SECONDS_MIN,
    SegmentTooLarge,
    build_ffmpeg_args,
    build_playlist,
    build_poster_args,
    parse_hls_index,
    parse_probe,
    segment_seconds,
    validate_segment_files,
)

PROBE_FIXTURE = {
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
    "format": {"duration": "123.45"},
}


def test_parse_probe_extracts_tech():
    info = parse_probe(PROBE_FIXTURE)
    assert info["duration"] == 123.45
    assert info["width"] == 1920
    assert info["height"] == 1080
    assert info["video_codec"] == "hevc"
    assert info["audio_codec"] == "aac"
    assert info["has_audio"] is True


def test_parse_probe_no_audio():
    fixture = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1, "height": 1}], "format": {}}
    info = parse_probe(fixture)
    assert info["has_audio"] is False
    assert info["audio_codec"] == ""
    assert info["duration"] == 0


def test_parse_probe_no_video_raises():
    try:
        parse_probe({"streams": [{"codec_type": "audio", "codec_name": "mp3"}], "format": {"duration": "1"}})
    except ValueError:
        return
    raise AssertionError("source without video must raise")


def test_ffmpeg_args_h264_aac_all_copy(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x" * 500_000)
    info = {"video_codec": "h264", "audio_codec": "aac", "has_audio": True, "duration": 1}
    args = build_ffmpeg_args(str(src), info, "/tmp/out", "src-abc")
    assert args[:4] == ["-y", "-hide_banner", "-i", str(src)]
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "copy"
    assert SEGMENT_SECONDS_MIN <= int(args[args.index("-hls_time") + 1]) <= SEGMENT_SECONDS_MAX
    assert args[args.index("-hls_playlist_type") + 1] == "vod"
    assert args[args.index("-hls_flags") + 1] == "independent_segments"
    assert args[args.index("-hls_segment_filename") + 1] == "/tmp/out/src-abc-%05d.ts"
    assert args[-1] == "/tmp/out/index.m3u8"


def test_ffmpeg_args_hevc_transcodes_video():
    info = {"video_codec": "hevc", "audio_codec": "aac", "has_audio": True}
    args = build_ffmpeg_args("/s", info, "/o", "p")
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-preset") + 1] == "medium"
    assert args[args.index("-crf") + 1] == "19"
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    # audio aac stays copy
    assert args[args.index("-c:a") + 1] == "copy"


def test_ffmpeg_args_mp3_audio_reencodes():
    info = {"video_codec": "h264", "audio_codec": "mp3", "has_audio": True}
    args = build_ffmpeg_args("/s", info, "/o", "p")
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "128k"
    assert args[args.index("-c:v") + 1] == "copy"


def test_ffmpeg_args_no_audio_omits_audio_flags():
    info = {"video_codec": "h264", "audio_codec": "", "has_audio": False}
    args = build_ffmpeg_args("/s", info, "/o", "p")
    assert "-c:a" not in args


def test_poster_args_align_old_mjs():
    args = build_poster_args("/src.mp4", "/tmp/poster.jpg", seek="1")
    assert args[:3] == ["-y", "-v", "error"]
    assert args[args.index("-ss") + 1] == "1"
    assert args[args.index("-frames:v") + 1] == "1"
    assert args[args.index("-vf") + 1] == "scale=480:-2"
    assert args[args.index("-q:v") + 1] == "3"
    assert args[-1] == "/tmp/poster.jpg"


def test_parse_hls_index_pairs_extinf_with_file():
    text = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.006,\nseg-00000.ts\n#EXTINF:5.5,\nseg-00001.ts\n#EXT-X-ENDLIST\n"
    segs = parse_hls_index(text)
    assert segs == [
        {"file": "seg-00000.ts", "duration": 6.006},
        {"file": "seg-00001.ts", "duration": 5.5},
    ]


def test_segment_seconds_clamps_low_bitrate_to_max(tmp_path):
    src = tmp_path / "low.mp4"
    src.write_bytes(b"x" * 125_000)  # 1 Mbps at 1s
    assert segment_seconds(str(src), {"duration": 1}) == SEGMENT_SECONDS_MAX


def test_segment_seconds_clamps_high_bitrate_to_min(tmp_path):
    src = tmp_path / "high.mp4"
    src.write_bytes(b"x" * 2_500_000)  # 20 Mbps at 1s
    assert segment_seconds(str(src), {"duration": 1}) == SEGMENT_SECONDS_MIN


def test_segment_seconds_targets_5mb(tmp_path):
    src = tmp_path / "mid.mp4"
    src.write_bytes(b"x" * 500_000)  # 4 Mbps at 1s -> 9s, clamped to 6
    assert segment_seconds(str(src), {"duration": 1}) == SEGMENT_SECONDS_MAX


def test_segment_seconds_targets_5mb_at_8mbps(tmp_path):
    src = tmp_path / "mid8.mp4"
    src.write_bytes(b"x" * 1_000_000)  # 8 Mbps at 1s
    assert segment_seconds(str(src), {"duration": 1}) == 4


def test_segment_seconds_falls_back_when_duration_missing(tmp_path):
    src = tmp_path / "unknown.mp4"
    src.write_bytes(b"x" * 10)
    assert segment_seconds(str(src), {}) == SEGMENT_SECONDS_MAX


def test_validate_segment_files_rejects_oversize(tmp_path):
    src = tmp_path / "big.ts"
    src.write_bytes(b"x" * 10)
    try:
        validate_segment_files([str(src)], limit=5)
    except SegmentTooLarge:
        return
    raise AssertionError("oversize segment must raise SegmentTooLarge")


def test_ffmpeg_args_copies_when_keyframe_gap_is_ten():
    info = {"video_codec": "h264", "audio_codec": "aac", "has_audio": True, "keyframe_gap": 10.0}
    args = build_ffmpeg_args("/s", info, "/o", "p")
    assert args[args.index("-c:v") + 1] == "copy"


def test_ffmpeg_args_transcodes_when_keyframe_gap_exceeds_ten():
    info = {"video_codec": "h264", "audio_codec": "aac", "has_audio": True, "keyframe_gap": 10.1}
    args = build_ffmpeg_args("/s", info, "/o", "p")
    assert args[args.index("-c:v") + 1] == "libx264"


def test_build_playlist_uses_relative_segment_urls():
    text = build_playlist([
        {"seq": 0, "tg_file_id": "AAA", "size": 100, "duration": 6.0, "bot_slot": 1, "mid": 42},
        {"seq": 1, "tg_file_id": "BBB", "size": 200, "duration": 5.5},
    ], "file-id")
    assert "000.ts" in text
    assert "001.ts" in text
    assert "?fid=" not in text
    assert "/dl/seg/" not in text
