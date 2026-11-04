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
    """HLS 切分参数：能 copy 就 copy，hevc/mp3 才重编码（对齐旧 pipeline.mjs）。"""
    args = ["-y", "-hide_banner", "-i", src]
    if info.get("video_codec") == "h264":
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if info.get("has_audio"):
        if info.get("audio_codec") == "aac":
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", "aac", "-b:a", "128k"]
    args += [
        "-hls_time", "20",
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


def probe(path: str) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr.strip()[:400]}")
    return parse_probe(json.loads(p.stdout))


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


def build_playlist(segments: list[dict], file_id: str) -> str:
    """按 plan §3.6 生成播放列表。

    segments 元素：{seq, tg_file_id, size, duration}；空列表是错误（0 分片的视频无意义）。
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
        lines.append(f"/dl/seg/{file_id}/{seq:03d}.ts?fid={seg['tg_file_id']}&sz={int(seg['size'])}")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def segment_rows(segments: list[dict], file_id: str) -> list[dict]:
    """本地分片清单 → 上报形状（seq 连续化 + 补齐 tg_file_id/size/duration）。"""
    rows = []
    for i, seg in enumerate(segments):
        rows.append({
            "seq": i,
            "tg_file_id": seg["tg_file_id"],
            "size": int(seg["size"]),
            "duration": float(seg["duration"]),
        })
    return rows


def playlist_stats(segments: list[dict]) -> dict:
    total = sum(float(s["duration"]) for s in segments)
    return {"segments": len(segments), "duration_s": round(total, 3)}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
