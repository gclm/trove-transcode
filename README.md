# trove-transcode

Trove 的 HLS 转码流水线：GitHub Actions 从 Cloudflare R2 下载源文件，用 ffmpeg 切成 6 秒 HLS 分片，逐片上传到 Telegram 私有频道，然后回调 Trove Worker 登记。

## 工作方式

1. 浏览器把源文件直传 R2（Trove Worker 签发的预签名 PUT URL）。
2. Trove Worker 通过 `repository_dispatch`（`trove-transcode`）触发本仓库，`client_payload` 携带：
   - `sourceId`：Trove D1 的 sources 记录 id
   - `sourceGetUrl` / `sourceDeleteUrl`：**有效期为 2 小时的预签名 URL**
   - `sourceName` / `apiBase`
3. Actions 下载源文件 → `ffprobe` 探测 → ffmpeg HLS 6s（H.264/AAC 直接 `-c copy`，HEVC 等转码 H.264）→ 逐片 `sendFile` 到 Telegram 频道 → 删除 R2 源文件 → `POST /api/hls/register`。
4. Trove Worker 把 `files` 与 `hls_segments` 写进 D1，前端用 xgplayer 播放 `/dl/hls/<mid>/index.m3u8`。

## 为什么用预签名 URL

- R2 的 Secret Access Key **不进 GitHub**：public 仓库的 secrets 虽然加密，但减少一个高权限凭据的暴露面。
- Actions 只需 `fetch(url)`，不依赖 S3 SDK / aws4fetch。
- URL 有效期 2 小时，覆盖排队与下载；过期后源文件由 R2 lifecycle 12 小时兜底清理。

## 需要的 GitHub Secrets

| Secret | 说明 |
| --- | --- |
| `TG_API_ID` | my.telegram.org 申请的 API ID |
| `TG_API_HASH` | my.telegram.org 申请的 API Hash |
| `TG_BOT_TOKEN` | @BotFather 创建的 bot token |
| `TG_CHANNEL` | 私有频道 id（`-100...`），bot 需为管理员 |
| `TROVE_API_KEY` | Trove Worker 的 `X-Api-Key`（legacy key） |

## 安全说明

本仓库为 public，Actions 日志公开。脚本不会打印预签名 URL；但 `repository_dispatch` 的 event payload 在 public 仓库可通过 GitHub API 查看，因此**只适合处理本就计划公开的媒体**。私密文件请改用 private 仓库或 Worker 中转方案。

## 本地调试

```bash
npm install
SOURCE_ID=... SOURCE_GET_URL=... SOURCE_DELETE_URL=... SOURCE_NAME=demo.mp4 \
API_BASE=https://api.trove.xzim.top TROVE_API_KEY=... \
TG_API_ID=... TG_API_HASH=... TG_BOT_TOKEN=... TG_CHANNEL=... \
node scripts/hls-transcode.mjs
```
