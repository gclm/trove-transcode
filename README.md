# trove-transcode

Trove 的 HLS 转码流水线。GitHub Actions 从 R2 拉取源文件，ffmpeg 切成 10 秒 HLS 分片，批量上传 Telegram，然后回调 Trove Worker 登记。

## 流程

```
浏览器 → R2（预签名 PUT）
      → Worker dispatch
      → Actions：下载源文件 → ffprobe → ffmpeg HLS 10s → Telegram
      → Worker：/api/hls/register 登记 + 删除 R2 源文件
```

## 需要的 GitHub Secrets

| 名称 | 用途 |
| --- | --- |
| `TROVE_API_BASE` | Trove Worker 地址 |
| `TROVE_API_KEY` | Worker API Key |
| `TG_API_ID` / `TG_API_HASH` | Telegram API 凭据 |
| `TG_BOT_TOKEN` | Bot token |
| `TG_CHANNEL` | 私有频道 id（`-100...`） |

## 安全设计

- 预签名 URL 不进 `client_payload`、不进日志：Actions 用 API Key 调 `/api/source/<id>/download`，Worker 返回 302（预签名 URL 30 分钟有效）。
- Actions 无 R2 写权限；R2 源文件由 Worker 在登记成功后删除，12 小时 lifecycle 兜底。
- 本仓库公开，仅适合处理公开媒体。

## 本地调试

```bash
npm install
SOURCE_ID=... SOURCE_NAME=demo.mp4 API_BASE=https://... TROVE_API_KEY=... \
TG_API_ID=... TG_API_HASH=... TG_BOT_TOKEN=... TG_CHANNEL=... \
node scripts/hls-transcode.mjs
```
