"""回报协议 env 自检契约测试：缺失 env 必须给出完整清单且不触网。"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE = ["SOURCE_ID", "API_BASE", "TROVE_API_KEY", "TG_API_ID", "TG_API_HASH", "TG_BOT_TOKEN", "TG_CHANNEL"]


def _run(job, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TROVE_", "SOURCE_", "TG_", "API_", "R2_", "DOUYIN_", "PORN91_", "RESOLVE_"))}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / job), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_upload_job_dry_run_lists_missing_env():
    p = _run("workers/upload/job.py", {})
    assert p.returncode == 2
    for key in CORE + ["R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]:
        assert key in p.stderr, f"{key} not reported missing"


def test_resolve_job_dry_run_lists_missing_env():
    p = _run("workers/resolve/job.py", {})
    assert p.returncode == 2
    for key in CORE:
        assert key in p.stderr


def test_collect_job_dry_run_ok_without_tg():
    # 编排 job 只需 API 凭据，不需要 Telethon/R2
    p = _run("workers/scheduled/collect91/job.py", {"API_BASE": "https://x.example", "TROVE_API_KEY": "k"})
    assert p.returncode == 0, p.stderr
    assert "[]" in p.stdout  # 空 fresh 输出合法 shards JSON


def test_all_env_present_dry_run_exits_zero():
    dummy = {k: "dummy" for k in CORE}
    dummy.update({"R2_ENDPOINT": "https://r2.example", "R2_ACCESS_KEY_ID": "id", "R2_SECRET_ACCESS_KEY": "sec", "R2_BUCKET": "b"})
    p = _run("workers/upload/job.py", dummy)
    assert p.returncode == 0, p.stderr


def test_require_env_returns_full_missing_list():
    from lib.api import require_env
    missing = require_env(["A", "B", "C"], {"B": "x"})
    assert missing == ["A", "C"]
