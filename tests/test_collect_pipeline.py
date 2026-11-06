"""collect job / pipeline 的编排契约：一次 batch 建任务、片内只解析一次、一次状态回报。"""
from __future__ import annotations

import json

import pytest

from workers.scheduled.collect91 import job as collect_job
from workers.scheduled.collect91 import pipeline


class FakeCollectClient:
    def __init__(self):
        self.batch_calls = []

    def runner_config(self):
        return {
            "tg_api_id": 1234567,
            "tg_api_hash": "0123456789abcdef0123456789abcdef",
            "tg_channel": "-1001234567890",
            "bots": [{"token": "111111:" + "A" * 35, "bot_name": "main", "master": True}],
            "cookies": {"PORN91_COOKIE": "test-cookie"},
        }

    def folders(self):
        return [{"id": "f1", "name": "91pron"}]

    def create_folder(self, name):
        return {"id": "f2", "name": name}

    def create_tasks_batch(self, items, folder_id=None):
        self.batch_calls.append({"items": items, "folder_id": folder_id})
        return {
            "created": [
                {"id": f"s{i}", "url": row["url"], "external_key": row["external_key"]}
                for i, row in enumerate(items)
            ],
            "skipped": [],
        }


def test_collect_calls_batch_once_and_emits_6_slots(monkeypatch, capsys):
    keys = [f"91porn:viewkey={i:05d}" for i in range(72)]
    fake = FakeCollectClient()
    monkeypatch.setattr(collect_job, "_91", type("S", (), {
        "pick_base_sync": staticmethod(lambda **kwargs: "https://91porn.com"),
        "list_category": staticmethod(lambda base, **kwargs: keys),
        "entry_from_key": staticmethod(lambda key, base=None: f"https://example/{key}"),
    }))
    monkeypatch.setattr(collect_job.api, "TroveApi", lambda base, key: fake)
    monkeypatch.setenv("API_BASE", "http://test")
    monkeypatch.setenv("TROVE_API_KEY", "k")

    assert collect_job.main() == 0
    out = capsys.readouterr().out.strip()
    shards = json.loads(out)
    assert len(fake.batch_calls) == 1
    assert len(fake.batch_calls[0]["items"]) == 72
    assert len(shards) == 6
    assert all(len(s["items"]) == 12 for s in shards)
    assert all(s["slot"] == 0 for s in shards)  # 默认只配主 bot


class FakePipelineClient:
    def __init__(self):
        self.patches = []

    def fetch_info(self, source_id):
        return {"id": source_id, "mode": "resolve", "source_url": "https://example/v", "name": "fallback"}

    def patch(self, source_id, **fields):
        self.patches.append({"source_id": source_id, **fields})


def test_pipeline_resolves_once_and_confirms_once(monkeypatch):
    calls = []
    main_token = "111111:" + "A" * 35
    monkeypatch.setenv("BOTS", json.dumps([{"token": main_token, "bot_name": "main", "master": True}]))
    monkeypatch.setattr(pipeline.resolve, "resolve_with_retry", lambda link: {
        "title": "T", "duration_s": 12, "poster_url": "p", "external_key": "k",
        "download_url": "d", "download_headers": {},
    })
    monkeypatch.setattr(pipeline.resolve, "site_of", lambda link: "91porn")
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pipeline, "upload_process", lambda *a, **kw: calls.append(kw))

    client = FakePipelineClient()
    result = pipeline.run_shard(client, {"slot": 0, "items": [{"source_id": "s1", "key": "k1"}]}, "chat")

    assert result == {"ok": 1, "failed": []}
    assert len(client.patches) == 1
    assert client.patches[0]["status"] == "confirmed"
    assert client.patches[0]["external_key"] == "k"
    assert calls[0]["meta"]["download_url"] == "d"
    assert calls[0]["upload_token"] == main_token
    assert calls[0]["upload_slot"] == 0


def test_helper_slot_without_token_fails_fast(monkeypatch):
    monkeypatch.setenv("BOTS", json.dumps([{"token": "111111:" + "A" * 35, "bot_name": "main"}]))
    with pytest.raises(RuntimeError, match="bot slot 1 is not configured"):
        pipeline._upload_token_for_slot(1)
