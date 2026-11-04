# trove-transcode

Trove 的 HLS 转码流水线。GitHub Actions 从 R2 拉取源文件，ffmpeg 按目标 15MB 自适应切成 HLS 分片（硬上限 19MB），批量上传 Telegram，然后回调 Trove Worker 登记。

## 流程

```
浏览器 → R2（预签名 PUT）
      → Worker dispatch
      → Actions：下载源文件 → ffprobe → ffmpeg HLS 自适应分片 → Telegram
      → Worker：/api/hls/register 登记 + 删除 R2 源文件
```

## 需要的 GitHub Secrets

只保留两个引导凭据；Telegram / 解析 cookie 等全部在 Worker 的 `runner-config` 中下发：

| 名称 | 用途 |
| --- | --- |
| `TROVE_API_BASE` | Trove Worker 地址 |
| `TROVE_API_KEY` | Worker 部署 key（同时用于拉取 runner 配置） |

`BOTS`、`TG_API_ID`、`TG_API_HASH`、`TG_CHANNEL`、`TG_BOT_TOKEN`、`R2_*`、`PORN91_COOKIE`、`DOUYIN_COOKIE`、`XHS_COOKIE`、`HAIJIAO_COOKIE` 都只存 Worker，不在本仓库配置。

## 安全设计

- runner 启动时调 `GET /api/v1/runner-config` 拉取 Telegram 与解析凭据；该响应含 bot token，禁止打印或写入日志。
- 预签名 URL 不进 `client_payload`、不进日志：Actions 用 API Key 调 `/api/source/<id>/download`，Worker 返回 302（预签名 URL 30 分钟有效）。
- Actions 无 R2 写权限；R2 源文件由 Worker 在登记成功后删除，12 小时 lifecycle 兜底。
- 本仓库公开，仅适合处理公开媒体。

## 本地调试

```bash
SOURCE_ID=... API_BASE=https://... TROVE_API_KEY=... \
python workers/upload/job.py --dry-run
```

正式运行只需 `SOURCE_ID`、`API_BASE`、`TROVE_API_KEY`；其余配置由 runner 从 Worker 拉取。
