/**
 * Trove url-mode pipeline (link resolver tasks).
 *
 * Flow: pull the resolved direct link from the Worker's authenticated
 * /api/v1/tasks/<id>/fetch-info endpoint -> stream it to disk (never buffered
 * whole in memory; sources reach hundreds of MB) -> shared pipeline
 * (probe / ffmpeg HLS / Telegram upload / register).
 *
 * The direct link never appears in the dispatch payload or workflow logs:
 * it travels only through fetch-info, which answers 409/410 once the task
 * is finished, so stale links cannot be enumerated.
 *
 * Required env:
 *   SOURCE_ID, API_BASE, TROVE_API_KEY (SOURCE_NAME optional; fetch-info supplies it)
 *   TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHANNEL
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { createPipeline } from "./lib/pipeline.mjs";
import { resolveOnRunner } from "./lib/resolvers.mjs";

const env = process.env;
const need = (k) => {
  const v = env[k];
  if (!v) throw new Error(`missing env ${k}`);
  return v;
};

const SOURCE_ID = need("SOURCE_ID");
const API_BASE = need("API_BASE").replace(/\/+$/, "");
const API_KEY = need("TROVE_API_KEY");
const TG_API_ID = Number(need("TG_API_ID"));
const TG_API_HASH = need("TG_API_HASH");
const TG_BOT_TOKEN = need("TG_BOT_TOKEN");
const TG_CHANNEL = need("TG_CHANNEL");

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "trove-url-"));
const outDir = path.join(workDir, "hls");
// Resolved names come from the page title and may contain odd characters;
// keep a plain extension-only temp name and sanitize for Telegram later.
const srcPath = path.join(workDir, "source.mp4");

function log(...args) {
  console.log(`[trove-url ${new Date().toISOString()}]`, ...args);
}

async function fetchInfo() {
  const res = await fetch(`${API_BASE}/api/v1/tasks/${SOURCE_ID}/fetch-info`, {
    headers: { Authorization: `Bearer ${API_KEY}` },
    redirect: "follow",
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`fetch-info failed: HTTP ${res.status} ${text.slice(0, 300)}`);
  // v1 envelope: {data:{...}}. needsResolve = the Worker could not resolve this
  // page itself; the runner will resolve it. Neither url nor needsResolve is bad.
  const data = JSON.parse(text)?.data;
  if (!data || (!data.url && !data.needs_resolve)) {
    throw new Error(`fetch-info returned no url: ${text.slice(0, 300)}`);
  }
  return data;
}

async function downloadUrl(linkInfo) {
  log("download remote source");
  const res = await fetch(linkInfo.url, {
    headers: { ...(linkInfo.headers || {}), "User-Agent": linkInfo.headers?.["User-Agent"] || "Mozilla/5.0" },
    redirect: "follow",
  });
  if (!res.ok) {
    // 403/410 from the CDN usually means the short-lived link expired; say so explicitly.
    const hint = res.status === 403 || res.status === 410 ? " (direct link likely expired — resubmit the link)" : "";
    throw new Error(`remote download failed: HTTP ${res.status}${hint}`);
  }
  const out = fs.createWriteStream(srcPath);
  let size = 0;
  for await (const chunk of res.body) {
    size += chunk.length;
    out.write(chunk);
  }
  await new Promise((r) => out.end(r));
  log("downloaded", size, "bytes");
  return size;
}

function sanitizeName(name) {
  const clean = String(name || "").replace(/[\r\n"\\]/g, "_").trim();
  return clean || "video.mp4";
}

async function main() {
  try {
    const fetched = await fetchInfo();
    let linkInfo;
    if (fetched.needs_resolve) {
      // Worker egress is blocked for this site; resolve the page here on the runner.
      log(`runner-side resolve for site=${fetched.site}`);
      const resolved = await resolveOnRunner(fetched.site, fetched.source_url);
      linkInfo = { name: sanitizeName(resolved.title).replace(/\.mp4$/i, "") + ".mp4", url: resolved.url, headers: resolved.headers };
      if (resolved.durationSec) log("resolved duration", resolved.durationSec);
    } else {
      linkInfo = fetched;
    }
    // The resolved page title may arrive after job start, so the pipeline is
    // built here: captions and register must carry the real name.
    const pipeline = createPipeline({
      sourceId: SOURCE_ID,
      sourceName: sanitizeName(linkInfo.name),
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
    await pipeline.report({ status: "downloading", message: "workflow started" });
    await downloadUrl(linkInfo);
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
    try {
      await fetch(`${API_BASE}/api/v1/tasks/${SOURCE_ID}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${API_KEY}` },
        body: JSON.stringify({ status: "error", error: String(e?.message || e).slice(0, 1000) }),
      });
    } catch { /* best effort */ }
    process.exitCode = 1;
  } finally {
    try { fs.rmSync(workDir, { recursive: true, force: true }); } catch {}
  }
}

main().finally(() => {
  setTimeout(() => process.exit(process.exitCode ?? 0), 250).unref();
});
