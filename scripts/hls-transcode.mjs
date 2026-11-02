/**
 * Trove HLS transcode pipeline.
 *
 * Flow: download source from R2 via a pre-signed GET URL -> ffmpeg HLS 6s
 * segments (copy when H.264/AAC, transcode otherwise) -> upload every .ts
 * segment to Telegram -> delete the R2 source via a pre-signed DELETE URL ->
 * POST /api/hls/register so the Worker can serve /dl/hls/<mid>/index.m3u8.
 *
 * No R2 credentials live in this public repo: the Worker signs short-lived
 * URLs and passes them in the repository_dispatch payload.
 *
 * Required env:
 *   SOURCE_ID, SOURCE_GET_URL, SOURCE_DELETE_URL, SOURCE_NAME, API_BASE
 *   TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHANNEL
 *   TROVE_API_KEY (legacy X-Api-Key of the Worker)
 */
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const env = process.env;
const need = (k) => {
  const v = env[k];
  if (!v) throw new Error(`missing env ${k}`);
  return v;
};

const SOURCE_ID = need("SOURCE_ID");
const SOURCE_GET_URL = need("SOURCE_GET_URL");
const SOURCE_DELETE_URL = need("SOURCE_DELETE_URL");
const SOURCE_NAME = env.SOURCE_NAME || "video.mp4";
const API_BASE = need("API_BASE").replace(/\/+$/, "");
const API_KEY = need("TROVE_API_KEY");

const TG_API_ID = Number(need("TG_API_ID"));
const TG_API_HASH = need("TG_API_HASH");
const TG_BOT_TOKEN = need("TG_BOT_TOKEN");
const TG_CHANNEL = need("TG_CHANNEL");

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "trove-hls-"));
const srcPath = path.join(workDir, "source" + (path.extname(SOURCE_NAME) || ".mp4"));
const outDir = path.join(workDir, "hls");

function log(...args) {
  console.log(`[trove-hls ${new Date().toISOString()}]`, ...args);
}

function run(cmd, args, opts = {}) {
  log(cmd, args.join(" "));
  return execFileSync(cmd, args, { stdio: "inherit", maxBuffer: 64 * 1024 * 1024, ...opts });
}

async function report(patch) {
  try {
    const res = await fetch(`${API_BASE}/api/hls/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Api-Key": API_KEY },
      body: JSON.stringify({ sourceId: SOURCE_ID, ...patch }),
    });
    log("status report", res.status, await res.text().then((t) => t.slice(0, 300)));
  } catch (e) {
    log("status report failed", e?.message || e);
  }
}

async function downloadSource() {
  log("download source (pre-signed)");
  const res = await fetch(SOURCE_GET_URL);
  if (!res.ok) throw new Error(`R2 download failed: HTTP ${res.status} ${await res.text().catch(() => "")}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(srcPath, buf);
  log("downloaded", buf.length, "bytes ->", srcPath);
}

async function deleteSource() {
  try {
    const res = await fetch(SOURCE_DELETE_URL, { method: "DELETE" });
    log("delete source", res.status === 204 ? "ok" : `HTTP ${res.status}`);
  } catch (e) {
    log("delete source failed (12h lifecycle will clean it)", e?.message || e);
  }
}

function probe() {
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

function transcode(info) {
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
    "-hls_time", "6",
    "-hls_playlist_type", "vod",
    "-hls_flags", "independent_segments",
    "-hls_segment_filename", path.join(outDir, "seg%05d.ts"),
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

async function uploadToTelegram(segs) {
  const client = new TelegramClient(new StringSession(""), TG_API_ID, TG_API_HASH, {
    connectionRetries: 5,
    autoReconnect: false,
  });
  client.setLogLevel("error");
  await client.start({ botAuthToken: TG_BOT_TOKEN });
  log("telegram connected as bot");

  const uploaded = [];
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i];
    const size = fs.statSync(seg.file).size;
    let msg = null;
    for (let attempt = 1; attempt <= 4; attempt++) {
      try {
        msg = await client.sendFile(TG_CHANNEL, {
          file: seg.file,
          forceDocument: true,
          workers: 4,
          caption: `${SOURCE_NAME} · HLS ${String(i + 1).padStart(4, "0")}`,
        });
        break;
      } catch (e) {
        log(`upload segment ${i} attempt ${attempt} failed: ${e?.message || e}`);
        if (attempt === 4) throw e;
        await new Promise((r) => setTimeout(r, 3000 * attempt));
      }
    }
    uploaded.push({ seq: i, tg_mid: msg.id, size, duration: seg.duration });
    log(`segment ${i + 1}/${segs.length} uploaded (tg_mid=${msg.id}, ${size} bytes)`);
  }
  await client.disconnect().catch(() => {});
  return uploaded;
}

async function register(info, segs) {
  const res = await fetch(`${API_BASE}/api/hls/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Api-Key": API_KEY },
    body: JSON.stringify({
      sourceId: SOURCE_ID,
      name: SOURCE_NAME,
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

async function main() {
  await report({ status: "transcoding", message: "workflow started" });
  try {
    await downloadSource();
    const info = probe();
    log("probe", JSON.stringify(info));
    transcode(info);
    const segs = parseSegments();
    log("segments", segs.length);
    const uploaded = await uploadToTelegram(segs);
    await deleteSource();
    await register(info, uploaded);
    log("DONE");
  } catch (e) {
    log("FAILED", e?.stack || e?.message || e);
    await report({ status: "error", error: String(e?.message || e).slice(0, 1000) });
    process.exitCode = 1;
  } finally {
    try { fs.rmSync(workDir, { recursive: true, force: true }); } catch {}
  }
}

main();
