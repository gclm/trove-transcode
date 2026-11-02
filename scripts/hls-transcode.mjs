/**
 * Trove HLS transcode pipeline (R2-staged uploads).
 *
 * Flow: download source from R2 via the Worker's authenticated 302 ->
 * shared pipeline (probe / ffmpeg HLS / Telegram upload / register).
 * The Worker deletes the R2 source copy after a successful register.
 *
 * No R2 credentials live in this public repo: the Worker signs short-lived
 * URLs and hands them out only through /api/source/<id>/download.
 *
 * Required env:
 *   SOURCE_ID, SOURCE_NAME, API_BASE, TROVE_API_KEY
 *   TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHANNEL
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { createPipeline } from "./lib/pipeline.mjs";

const env = process.env;
const need = (k) => {
  const v = env[k];
  if (!v) throw new Error(`missing env ${k}`);
  return v;
};

const SOURCE_ID = need("SOURCE_ID");
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

const pipeline = createPipeline({
  sourceId: SOURCE_ID,
  sourceName: SOURCE_NAME,
  apiBase: API_BASE,
  apiKey: API_KEY,
  tgApiId: TG_API_ID,
  tgApiHash: TG_API_HASH,
  tgBotToken: TG_BOT_TOKEN,
  tgChannel: TG_CHANNEL,
  workDir,
  outDir,
  log,
});

async function downloadSource() {
  // Authenticated endpoint; the Worker answers 302 to a short-lived presigned URL.
  // Nothing secret is ever printed or stored in the public workflow logs.
  log("download source");
  const res = await fetch(`${API_BASE}/api/source/${SOURCE_ID}/download`, {
    headers: { "X-Api-Key": API_KEY },
    redirect: "follow",
  });
  if (!res.ok) throw new Error(`download failed: HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(srcPath, buf);
  log("downloaded", buf.length, "bytes");
}

async function main() {
  await pipeline.report({ status: "downloading", message: "workflow started" });
  try {
    await downloadSource();
    await pipeline.report({ status: "transcoding", message: "source downloaded" });
    const info = pipeline.probe(srcPath);
    log("probe", JSON.stringify(info));
    pipeline.transcode(srcPath, info);
    const posterPath = pipeline.makePoster(srcPath);
    const segs = pipeline.parseSegments();
    log("segments", segs.length);
    await pipeline.report({ status: "uploading_tg", message: `0/${segs.length} segments` });
    const { uploaded, sourceMid, sourceSize, posterMid } = await pipeline.uploadToTelegram(srcPath, segs, posterPath);
    await pipeline.register(info, uploaded, sourceMid, sourceSize, posterMid);
    log("DONE");
  } catch (e) {
    log("FAILED", e?.stack || e?.message || e);
    await pipeline.report({ status: "error", error: String(e?.message || e).slice(0, 1000) });
    process.exitCode = 1;
  } finally {
    try { fs.rmSync(workDir, { recursive: true, force: true }); } catch {}
  }
}

main().finally(() => {
  // GramJS leaves its update-loop timer alive after disconnect; exit explicitly so
  // GitHub Actions logs stay clean (job status still follows process.exitCode).
  setTimeout(() => process.exit(process.exitCode ?? 0), 250).unref();
});
