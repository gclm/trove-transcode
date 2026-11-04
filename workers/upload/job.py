"""上传 worker：一个已确认任务 → TG 归档（plan §1 / §4.2）。

按任务的真实内容分流（服务端已定通道，这里只执行）：
- 有视频流 → 转 HLS：分片走 Bot API 拿 file_id、playlist 归档、原档走 MTProto；
- 无视频流 → 原样 MTProto 直传（tg_mid），走 /tasks/:id/result 回报。

为什么 playlist 由本 job 生成：分片 file_id 只有上传后才存在，而 playlist 必须内嵌
`/dl/seg/{file_id}/{seq}.ts?fid=&sz=`——所以文件 id 由本 job 生成（uuidv7，作幂等键），
先拼 URL 再 PUT hls，服务端按该 id 落库。
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib import api, config, download, resolve, tg, transcode  # noqa: E402
from lib.uuid7 import uuid7  # noqa: E402

CORE = ["SOURCE_ID", "API_BASE", "TROVE_API_KEY"]


def _kind_of(mime: str) -> str:
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    return "file"


def _file_name(name: str | None, mime: str | None, fallback: str) -> str:
    """任务名 → 合法文件名：保留原名，缺扩展名时按 MIME 补。"""
    base = os.path.basename((name or "").strip()) or fallback
    if not os.path.splitext(base)[1]:
        ext = mimetypes.guess_extension(mime or "") or ""
        if ext == ".jpe":
            ext = ".jpg"
        base += ext
    return base


def _source_of(client: api.TroveApi, info: dict, meta: dict | None = None) -> tuple[str, dict]:
    """拿到可下载直链与请求头。

    采集 pipeline 已解析过页面，会把 meta 传进来复用（每条只解析一次）；
    standalone upload worker 没有 meta 时才按 source_url 重新解析。
    """
    if info.get("mode") == "resolve" and info.get("source_url"):
        if meta is None:
            meta = resolve.resolve_with_retry(info["source_url"])
        return meta["download_url"], meta.get("download_headers") or {}
    url = info.get("url") or ""
    if not url:
        raise RuntimeError("task has no downloadable url")
    return url, info.get("headers") or {}


def run() -> int:
    client = api.TroveApi(os.environ["API_BASE"], os.environ["TROVE_API_KEY"])
    config.bootstrap(client)
    process(client, os.environ["SOURCE_ID"], os.environ["TG_CHANNEL"])
    return 0


def process(client: api.TroveApi, source_id: str, chat_id: str, meta: dict | None = None, upload_token: str | None = None, upload_slot: int = 0) -> None:
    """把一个任务从「已确认」推到「已归档」；采集编排也复用这条路径。"""
    workdir = tempfile.mkdtemp(prefix="trove-upload-")
    try:
        info = client.fetch_info(source_id)
        name = info.get("name") or f"{source_id}.bin"
        mime = info.get("mime") or "application/octet-stream"
        file_name = _file_name(name, mime, f"{source_id}.bin")
        client.patch(source_id, status="downloading")

        # 通道 V/R 的源文件在 R2 暂存；没有暂存就回源直链。
        staged = client.artifact_url(source_id) if info.get("staged") else None
        headers: dict = {}
        if not staged:
            staged, headers = _source_of(client, info, meta)
        local = os.path.join(workdir, "source" + os.path.splitext(file_name)[1])
        download.download(staged, local, headers=headers)

        size = os.path.getsize(local)
        try:
            probe = transcode.probe(local)
        except Exception:
            probe = None  # 非视频或 ffprobe 不可用：按原样直传

        if probe and probe.get("height"):
            _upload_hls(client, source_id, local, workdir, name, file_name, mime, size, probe, chat_id, info.get("folder_id"), upload_token, upload_slot)
        else:
            client.patch(source_id, status="uploading")
            mid = tg.mtproto_upload(local, chat_id, caption=None, filename=file_name, token=upload_token)
            client.put_result(source_id, {
                "tg_mid": mid,
                "name": name,
                "size": size,
                "mime": mime,
                "kind": _kind_of(mime),
                "folder_id": info.get("folder_id"),
            })
        print(json.dumps({"ok": True, "source_id": source_id, "size": size}, ensure_ascii=False))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _upload_hls(client: api.TroveApi, source_id: str, local: str, workdir: str, name: str, file_name: str, mime: str, size: int, probe: dict, chat_id: str, info_folder: str | None = None, upload_token: str | None = None, upload_slot: int = 0) -> None:
    client.patch(source_id, status="transcoding")
    file_id = uuid7()
    segdir = os.path.join(workdir, "hls")
    parts = transcode.transcode_to_hls(local, segdir, "seg", probe)

    client.patch(source_id, status="uploading")
    seg_paths = [os.path.join(segdir, p["file"]) for p in parts]
    transcode.validate_segment_files(seg_paths)

    # 原档 MTProto 上传与分片 Bot API 上传并行；两者共用该 shard 的 bot，
    # playlist/封面仍走主 bot，避免主 bot 被分片流量拖慢。
    pool = ThreadPoolExecutor(max_workers=1)
    original = pool.submit(tg.mtproto_upload, local, chat_id, None, 6, file_name, upload_token)
    try:
        receipts = tg.bot_send_media_group(seg_paths, chat_id, token=upload_token)
        if len(receipts) != len(seg_paths):
            raise RuntimeError(f"segment upload mismatch: {len(receipts)}/{len(seg_paths)}")
        segments = transcode.segment_rows(
            [{"tg_file_id": r["file_id"], "size": os.path.getsize(p), "duration": part["duration"]}
             for r, p, part in zip(receipts, seg_paths, parts)],
            file_id,
            bot_slot=upload_slot,
        )
        if not all(s["tg_file_id"] for s in segments):
            raise RuntimeError("segment upload returned no file_id")

        playlist = transcode.build_playlist(segments, file_id)
        playlist_path = os.path.join(workdir, "index.m3u8")
        with open(playlist_path, "w", encoding="utf-8") as fh:
            fh.write(playlist)
        # Worker 侧走 Bot API getFile(file_id) 读回 playlist —— 存 file_id，不存 message_id。
        # playlist 与封面合成一组批量上传：一条 sendMediaGroup 拿两个 file_id，少一次 Bot 调用。
        poster_path = os.path.join(workdir, "poster.jpg")
        meta_paths = [playlist_path]
        if transcode.make_poster(local, poster_path):
            meta_paths.append(poster_path)
        if len(meta_paths) > 1:
            meta_receipts = tg.bot_send_media_group(meta_paths, chat_id)
            if len(meta_receipts) != 2 or not all(r.get("file_id") for r in meta_receipts):
                raise RuntimeError("playlist/poster upload returned no file_id")
            m3u8_file_id = meta_receipts[0]["file_id"]
            poster_file_id = meta_receipts[1]["file_id"]
        else:
            m3u8_file_id = tg.bot_send_document(playlist_path, chat_id, filename=f"{file_id}.m3u8")["file_id"]
            poster_file_id = None
        if not m3u8_file_id:
            raise RuntimeError("playlist upload returned no file_id")

        # 原档也归档：详情页「原档播放」直取，不依赖 HLS。
        tg_mid = original.result()
    finally:
        pool.shutdown(wait=True)

    client.put_hls(source_id, {
        "file_id": file_id,
        "tg_mid": tg_mid,
        "m3u8_file_id": m3u8_file_id,
        "duration_s": round(probe["duration"]) or transcode.playlist_stats(segments)["duration_s"],
        "height": probe["height"],
        "codec": probe["video_codec"],
        "poster_file_id": poster_file_id,
        "name": name,
        "size": size,
        "mime": mime or "video/mp4",
        "folder_id": info_folder,
    })

def main() -> int:
    api.env_or_die(CORE)
    if "--dry-run" in sys.argv:
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
