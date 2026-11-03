// Direct mode: non-video files are uploaded to Telegram as-is (no transcoding).
// The Worker (master) dispatches this job; this script (worker) downloads the
// staged R2 object through the Worker's authenticated redirect, uploads one
// Telegram document, and reports back so the master can delete the R2 copy.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";

const API_BASE = process.env.API_BASE || "";
const API_KEY = process.env.TROVE_API_KEY || "";
const SOURCE_ID = process.env.SOURCE_ID || "";
const SOURCE_NAME = process.env.SOURCE_NAME || "file";
const TG_API_ID = Number(process.env.TG_API_ID);
const TG_API_HASH = process.env.TG_API_HASH;
const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN;
const TG_CHANNEL = process.env.TG_CHANNEL;

const log = (...a) => console.log(...a);
const need = (v, name) => { if (!v) throw new Error(`missing env ${name}`); return v; };

function kindOf(mime) {
  if (/^image\//i.test(mime)) return "image";
  if (/^video\//i.test(mime)) return "video";
  if (/^audio\//i.test(mime)) return "audio";
  return "file";
}

async function download(dir) {
  const res = await fetch(`${API_BASE}/api/v1/tasks/${SOURCE_ID}/artifact`, {
    headers: { Authorization: `Bearer ${API_KEY}` },
    redirect: "follow",
  });
  if (!res.ok) throw new Error(`download failed: HTTP ${res.status}`);
  const dest = path.join(dir, "source.bin");
  const out = fs.createWriteStream(dest);
  for await (const chunk of res.body) out.write(chunk);
  await new Promise((r) => out.end(r));
  const size = fs.statSync(dest).size;
  log(`downloaded ${size} bytes`);
  return { path: dest, size };
}

async function report(status, message) {
  try {
    await fetch(`${API_BASE}/api/v1/tasks/${SOURCE_ID}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${API_KEY}` },
      body: JSON.stringify({ status, message }),
    });
  } catch { }
}

async function main() {
  need(API_BASE, "API_BASE"); need(API_KEY, "TROVE_API_KEY");
  need(SOURCE_ID, "SOURCE_ID"); need(TG_API_ID, "TG_API_ID");
  need(TG_API_HASH, "TG_API_HASH"); need(TG_BOT_TOKEN, "TG_BOT_TOKEN");
  need(TG_CHANNEL, "TG_CHANNEL");

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "trove-direct-"));
  try {
    await report("downloading", "downloading source");
    const src = await download(dir);

    await report("uploading_tg", "uploading to Telegram");
    const client = new TelegramClient(new StringSession(""), TG_API_ID, TG_API_HASH, {
      connectionRetries: 6,
      autoReconnect: false,
    });
    client.setLogLevel("error");
    let msg = null;
    for (let attempt = 1; attempt <= 6; attempt++) {
      try {
        await client.start({ botAuthToken: TG_BOT_TOKEN });
        msg = await client.sendFile(TG_CHANNEL, {
          file: src.path,
          forceDocument: true,
          workers: 4,
          caption: `${SOURCE_NAME}`,
        });
        break;
      } catch (e) {
        const m = String(e?.message || e);
        log(`upload attempt ${attempt} failed: ${m}`);
        const wait = m.match(/wait of (\d+) seconds/);
        if (wait) await new Promise((r) => setTimeout(r, (Number(wait[1]) + 5) * 1000));
        else if (attempt < 6) await new Promise((r) => setTimeout(r, 3000 * attempt));
        else throw e;
      }
    }
    await client.disconnect().catch(() => {});
    const doc = msg?.document || msg?.media?.document;
    const size = Number(doc?.size || src.size);
    const mime = String(doc?.mimeType || "application/octet-stream");
    log(`uploaded mid=${msg.id} size=${size} mime=${mime}`);

    const res = await fetch(`${API_BASE}/api/v1/tasks/${SOURCE_ID}/result`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${API_KEY}` },
      body: JSON.stringify({
        mid: msg.id,
        name: SOURCE_NAME,
        size,
        mime,
        kind: kindOf(mime),
      }),
    });
    const text = await res.text();
    log("register", res.status, text.slice(0, 300));
    if (!res.ok) throw new Error(`register failed: ${text}`);
    log("DONE");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
