"""ffmpeg/ffprobe 原语与 m3u8 生成（plan §3.6 / §4.1）。

分片与 playlist 的分工：
- ffmpeg 只负责切 ts；
- playlist 由 build_playlist 生成，URL 即数据（/dl/seg/{file_id}/{seq}.ts?fid=&sz=），
  播放侧零 D1 查询。
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from typing import Any

# Bot API getFile 的硬上限是 20MB；5MB 目标把首包和回源等待压到最低。
SEGMENT_TARGET_BYTES = 5_000_000
SEGMENT_MAX_BYTES = 19 * 1024 * 1024
SEGMENT_SECONDS_MIN = 3
SEGMENT_SECONDS_MAX = 6
# 关键帧间隔超过该阈值就重编码；copy 模式在部分源上会在关键帧边界跳切，实际分片超出 hls_time。
COPY_KEYFRAME_GAP_MAX = 10.0


class SegmentTooLarge(ValueError):
    """ffmpeg 切出的分片超过 Bot API getFile 的 20MB 上限。"""


def segment_seconds(src: str, info: dict) -> int:
    """按实际码率估算分片时长，把单分片压到 5MB 左右。

    码率优先用 file_size/duration；file_size 缺失时用 ffprobe 的 bit_rate
    （parse_probe 不输出它，这里直接读 info 或退回 2Mbps 保守值）。
    """
    duration = float(info.get("duration") or 0)
    size = 0
    try:
        size = os.path.getsize(src)
    except OSError:
        pass
    bit_rate = 0.0
    if duration > 0 and size > 0:
        bit_rate = size * 8 / duration
    if bit_rate <= 0:
        bit_rate = float(info.get("bit_rate") or 0)
    if bit_rate <= 0:
        bit_rate = 2_000_000
    seconds = int(SEGMENT_TARGET_BYTES * 8 / bit_rate * 0.9)
    return max(SEGMENT_SECONDS_MIN, min(SEGMENT_SECONDS_MAX, seconds))


def validate_segment_files(paths: list[str], limit: int = SEGMENT_MAX_BYTES) -> None:
    """上传前守住 Bot API 20MB 硬线；超限直接失败，让任务可重试而不是上传半残。"""
    for path in paths:
        size = os.path.getsize(path)
        if size > limit:
            raise SegmentTooLarge(f"segment {os.path.basename(path)} is {size} bytes (> {limit})")

# ---- ffprobe / ffmpeg ---------------------------------------------------------


def parse_probe(fixture: dict) -> dict:
    """ffprobe -show_streams -show_format 的 JSON → 技术元数据。

    没有视频流直接 ValueError：音频文件不该走 HLS 转码通道。
    """
    streams = fixture.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("source has no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = fixture.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration": duration,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio.get("codec_name") or "") if audio else "",
        "has_audio": audio is not None,
    }


def build_ffmpeg_args(src: str, info: dict, outdir: str, prefix: str) -> list[str]:
    """HLS 切分参数：关键帧足够密才 copy，否则重编码并强制关键帧对齐分片时长。

    只按 codec 判断会踩坑：h264 源的关键帧间隔经常 10–20s，copy + -hls_time
    只能切在关键帧上，实际分片会超过 SEGMENT_SECONDS_MAX，首片/拖动都会变慢。
    """
    args = ["-y", "-hide_banner", "-i", src]
    seg = segment_seconds(src, info)
    keyframe_gap = float(info.get("keyframe_gap") or 0)
    can_copy = info.get("video_codec") == "h264" and keyframe_gap <= COPY_KEYFRAME_GAP_MAX
    if can_copy:
        args += ["-c:v", "copy"]
    else:
        args += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{seg})",
        ]
    if info.get("has_audio"):
        if info.get("audio_codec") == "aac":
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", "aac", "-b:a", "128k"]
    args += [
        "-hls_time", str(seg),
        "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", os.path.join(outdir, f"{prefix}-%05d.ts"),
        os.path.join(outdir, "index.m3u8"),
    ]
    return args


def build_poster_args(src: str, out: str, seek: str = "1") -> list[str]:
    """封面截帧：1s 处取一帧缩到 480 宽（对齐旧 pipeline.mjs）。"""
    return ["-y", "-v", "error", "-ss", str(seek), "-i", src, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", out]


def parse_hls_index(text: str) -> list[dict]:
    """ffmpeg 产出的 index.m3u8 → [{file, duration}]，保持顺序。"""
    out: list[dict] = []
    pending: float | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            head = line[len("#EXTINF:"):].split(",", 1)[0]
            try:
                pending = float(head)
            except ValueError:
                pending = None
        elif line and not line.startswith("#") and pending is not None:
            out.append({"file": line, "duration": pending})
            pending = None
    return out


def run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def keyframe_gap(path: str) -> float:
    """最大关键帧间隔（秒）。无法探测时返回 0，让调用方退回 copy。"""
    p = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", path,
    ], timeout=120)
    if p.returncode != 0:
        return 0.0
    times = []
    for line in p.stdout.splitlines():
        try:
            times.append(float(line.strip().rstrip(",")))
        except ValueError:
            continue
    if len(times) < 2:
        return 1e9
    times.sort()
    return max(b - a for a, b in zip(times, times[1:]))


def probe(path: str) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr.strip()[:400]}")
    info = parse_probe(json.loads(p.stdout))
    info["keyframe_gap"] = keyframe_gap(path)
    return info


def transcode_to_hls(src: str, outdir: str, prefix: str, info: dict) -> list[dict]:
    """跑 ffmpeg 切 HLS，返回 [{file, duration}]（本地文件名，未上传）。"""
    os.makedirs(outdir, exist_ok=True)
    p = run(["ffmpeg", *build_ffmpeg_args(src, info, outdir, prefix)])
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.strip()[-600:]}")
    with open(os.path.join(outdir, "index.m3u8"), "r", encoding="utf-8") as fh:
        return parse_hls_index(fh.read())


def make_poster(src: str, out: str, seek: str = "1") -> bool:
    """截帧失败不算致命：返回 False 让调用方跳过封面。"""
    p = run(["ffmpeg", *build_poster_args(src, out, seek)], timeout=300)
    return p.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


# ---- playlist -----------------------------------------------------------------


def build_playlist(segments: list[dict], file_id: str = "") -> str:
    """按 PLAN-012 生成相对路径播放列表。

    segments 元素：{seq, tg_file_id, size, duration, bot_slot?, mid?}；空列表是错误（0 分片的视频无意义）。
    分片 URL 使用相对路径 `<seq>.ts`，由 /hls/<id>/index.m3u8 所在目录解析；
    tg_file_id/size/mid 由 job.py 随 PUT /tasks/:id/hls 上报到 D1 `segment_mids`。
    """
    if not segments:
        raise ValueError("build_playlist requires at least one segment")
    ordered = sorted(segments, key=lambda s: int(s["seq"]))
    target = int(math.ceil(max(float(s["duration"]) for s in ordered)))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f"#EXT-X-TARGETDURATION:{target}",
    ]
    for seg in ordered:
        seq = int(seg["seq"])
        lines.append(f"#EXTINF:{seg['duration']},")
        lines.append(f"{seq:03d}.ts")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def segment_rows(segments: list[dict], file_id: str, bot_slot: int = 0) -> list[dict]:
    """本地分片清单 → 上报形状（seq 连续化 + 补齐 tg_file_id/size/duration/bot_slot）。"""
    rows = []
    for i, seg in enumerate(segments):
        rows.append({
            "seq": i,
            "tg_file_id": seg["tg_file_id"],
            "size": int(seg["size"]),
            "duration": float(seg["duration"]),
            "bot_slot": int(seg.get("bot_slot", bot_slot)),
            "mid": int(seg.get("mid") or 0),
        })
    return rows


def playlist_stats(segments: list[dict]) -> dict:
    total = sum(float(s["duration"]) for s in segments)
    return {"segments": len(segments), "duration_s": round(total, 3)}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
