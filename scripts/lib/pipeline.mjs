/**
 * Shared transcode pipeline used by hls-transcode.mjs (R2-staged uploads) and
 * url-video.mjs (link-resolver downloads): status reporting, ffprobe, ffmpeg
 * HLS slicing, poster, Telegram upload and Worker registration. The caller only
 * provides the source file (and how it got there).
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";

export function createPipeline(opts) {
  const {
    sourceId,
    sourceName,
    apiBase,
    apiKey,
    tgApiId,
    tgApiHash,
    tgBotToken,
    tgChannel,
    workDir,
    outDir,
    log = (...a) => console.log(...a),
  } = opts;

  function run(cmd, args, execOpts = {}) {
    log(cmd, args.join(" "));
    return execFileSync(cmd, args, { stdio: "inherit", maxBuffer: 64 * 1024 * 1024, ...execOpts });
  }

  async function report(patch) {
    try {
      const res = await fetch(`${apiBase}/api/v1/tasks/${sourceId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify(patch),
      });
      log("status report", res.status, await res.text().then((t) => t.slice(0, 300)));
    } catch (e) {
      log("status report failed", e?.message || e);
    }
  }

  function probe(srcPath) {
    const raw = execFileSync("ffprobe", [
      "-v", "error", "-print_format", "json", "-show_format", "-show_streams", srcPath,
    ], { maxBuffer: 16 * 1024 * 1024 }).toString();
    const p = JSON.parse(raw);
    const video = p.streams.find((s) => s.codec_type === "video");
    const audio = p.streams.find((s) => s.codec_type === "audio");
    if (!video) throw new Error("source has no video stream");
    return {
      duration: Number(p.format?.duration) || 0,
      width: Number(video.width) || null,
      height: Number(video.height) || null,
      videoCodec: video.codec_name || "",
      audioCodec: audio?.codec_name || "",
      hasAudio: !!audio,
    };
  }

  function transcode(srcPath, info) {
    fs.mkdirSync(outDir, { recursive: true });
    const args = ["-y", "-hide_banner", "-i", srcPath];
    const copyVideo = info.videoCodec === "h264";
    const copyAudio = !info.hasAudio || info.audioCodec === "aac";

    if (copyVideo) {
      args.push("-c:v", "copy");
    } else {
      args.push("-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p");
    }
    if (copyAudio) {
      args.push("-c:a", "copy");
    } else {
      args.push("-c:a", "aac", "-b:a", "128k");
    }
    args.push(
      "-f", "hls",
      "-hls_time", "10",
      "-hls_playlist_type", "vod",
      "-hls_flags", "independent_segments",
      "-hls_segment_filename", path.join(outDir, `src-${sourceId.slice(0, 8)}-%05d.ts`),
      path.join(outDir, "index.m3u8"),
    );
    run("ffmpeg", args);
    log("ffmpeg done", copyVideo ? "(video copy)" : `(transcoded ${info.videoCodec} -> h264)`);
  }

  function parseSegments() {
    const text = fs.readFileSync(path.join(outDir, "index.m3u8"), "utf8");
    const list = [];
    let duration = null;
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      const m = /^#EXTINF:([\d.]+)/.exec(line);
      if (m) { duration = Number(m[1]); continue; }
      if (duration !== null && line && !line.startsWith("#")) {
        list.push({ file: path.join(outDir, line), duration });
        duration = null;
      }
    }
    if (!list.length) throw new Error("ffmpeg produced no HLS segments");
    return list;
  }

  function makePoster(srcPath) {
    const poster = path.join(workDir, "poster.jpg");
    const args = ["-y", "-v", "error", "-ss", "1", "-i", srcPath, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", poster];
    try {
      execFileSync("ffmpeg", args);
      if (!fs.existsSync(poster) || fs.statSync(poster).size === 0) {
        execFileSync("ffmpeg", ["-y", "-v", "error", "-ss", "0", "-i", srcPath, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", poster]);
      }
      log(`poster ${fs.statSync(poster).size} bytes`);
      return poster;
    } catch (e) {
      log("poster failed:", e?.message || e);
      return null;
    }
  }

  async function uploadToTelegram(srcPath, segs, posterPath) {
    const client = new TelegramClient(new StringSession(""), tgApiId, tgApiHash, {
      connectionRetries: 5,
      autoReconnect: false,
    });
    client.setLogLevel("error");
    // Bot authorization is rate-limited by Telegram (auth.ImportBotAuthorization);
    // back off and retry instead of failing the whole job.
    for (let attempt = 1; attempt <= 6; attempt++) {
      try {
        await client.start({ botAuthToken: tgBotToken });
        break;
      } catch (e) {
        const msg = String(e?.message || e);
        const m = msg.match(/wait of (\d+) seconds/);
        if (m && attempt < 6) {
          const wait = Number(m[1]) + 5;
          log(`telegram auth flood-wait ${wait}s (attempt ${attempt})`);
          await new Promise((r) => setTimeout(r, wait * 1000));
          continue;
        }
        throw e;
      }
    }
    log("telegram connected as bot");

    // v4: the original file is uploaded as ONE message (MTProto allows 2GB for bots,
    // unlike the Bot API 50MB limit). Its message id becomes the file mid, so
    // /dl/m/<mid> serves the original download without any extra mapping.
    let sourceMid = null;
    const srcSize = fs.statSync(srcPath).size;
    for (let attempt = 1; attempt <= 4; attempt++) {
      try {
        const msg = await client.sendFile(tgChannel, {
          file: srcPath,
          forceDocument: true,
          workers: 4,
          caption: `${sourceName} · 原文件`,
        });
        sourceMid = msg.id;
        break;
      } catch (e) {
        log(`upload source attempt ${attempt} failed: ${e?.message || e}`);
        if (attempt === 4) throw e;
        await new Promise((r) => setTimeout(r, 3000 * attempt));
      }
    }
    log(`source uploaded (mid=${sourceMid}, ${srcSize} bytes)`);
    let posterMid = null;
    if (posterPath) {
      for (let attempt = 1; attempt <= 3 && !posterMid; attempt++) {
        try {
          const pm = await client.sendFile(tgChannel, { file: posterPath, forceDocument: true, workers: 2, caption: "poster" });
          posterMid = pm.id;
        } catch (e) {
          log(`poster upload attempt ${attempt} failed: ${e?.message || e}`);
          if (attempt < 3) await new Promise((r) => setTimeout(r, 2000 * attempt));
        }
      }
      log(`poster uploaded (mid=${posterMid})`);
    }
    await report({ status: "uploading_tg", message: `source uploaded, segments 0/${segs.length}` });

    // v4: send up to 10 HLS segments per sendMediaGroup request (SendMultiMedia),
    // cutting the number of API calls by 10x.
    const uploaded = [];
    const BATCH = 10;
    for (let i = 0; i < segs.length; i += BATCH) {
      const chunk = segs.slice(i, i + BATCH);
      let msgs = null;
      for (let attempt = 1; attempt <= 4; attempt++) {
        try {
          msgs = await client.sendFile(tgChannel, {
            file: chunk.map((x) => x.file),
            forceDocument: true,
            workers: 4,
          });
          break;
        } catch (e) {
          log(`upload batch ${i} attempt ${attempt} failed: ${e?.message || e}`);
          if (attempt === 4) throw e;
          await new Promise((r) => setTimeout(r, 3000 * attempt));
        }
      }
      if (!Array.isArray(msgs)) msgs = [msgs];
      for (let j = 0; j < chunk.length; j++) {
        uploaded.push({ seq: i + j, tg_mid: msgs[j].id, size: fs.statSync(chunk[j].file).size, duration: chunk[j].duration });
      }
      log(`segments ${i + chunk.length}/${segs.length} uploaded`);
      await report({ status: "uploading_tg", message: `${i + chunk.length}/${segs.length} segments`, progress: { done: i + chunk.length, total: segs.length } });
    }
    await client.disconnect().catch(() => {});
    return { uploaded, sourceMid, sourceSize: srcSize, posterMid };
  }

  async function register(info, segs, sourceMid, sourceSize, posterMid) {
    const res = await fetch(`${apiBase}/api/v1/tasks/${sourceId}/hls`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        mid: sourceMid,
        size: sourceSize,
        poster_mid: posterMid,
        name: sourceName,
        mime: "video/mp4",
        duration: info.duration,
        width: info.width,
        height: info.height,
        segments: segs,
      }),
    });
    const text = await res.text();
    log("register", res.status, text.slice(0, 500));
    if (!res.ok) throw new Error(`register failed: HTTP ${res.status} ${text}`);
  }

  return { report, probe, transcode, parseSegments, makePoster, uploadToTelegram, register };
}
