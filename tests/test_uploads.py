"""上传 worker 的文件名规则：原文件名优先，缺扩展名按 MIME 补。"""
from workers.upload.job import _file_name


def test_file_name_keeps_original():
    assert _file_name("通知.pdf", "application/pdf", "fallback.bin") == "通知.pdf"


def test_file_name_adds_extension_from_mime():
    assert _file_name("潮吹女无CD 没有允许不让高潮", "video/mp4", "fallback.bin") == "潮吹女无CD 没有允许不让高潮.mp4"


def test_file_name_falls_back_when_empty():
    assert _file_name("", "image/jpeg", "task") == "task.jpg"


def test_file_name_strips_directory():
    assert _file_name("../evil/视频.mp4", "video/mp4", "x.bin") == "视频.mp4"


def test_source_of_prefers_pipeline_meta_without_resolving(monkeypatch):
    from workers.upload import job as upload_job

    def boom(*args, **kwargs):
        raise AssertionError("must not resolve when meta is supplied")

    monkeypatch.setattr(upload_job.resolve, "resolve_with_retry", boom)
    url, headers = upload_job._source_of(
        None,
        {"mode": "resolve", "source_url": "https://example/v"},
        {"download_url": "https://cdn/v.mp4", "download_headers": {"Referer": "x"}},
    )
    assert url == "https://cdn/v.mp4"
    assert headers == {"Referer": "x"}


def test_media_group_batches_respect_total_bytes(tmp_path):
    from lib import tg

    paths = []
    for i in range(4):
        p = tmp_path / f"seg-{i}.ts"
        p.write_bytes(b"x" * 20)
        paths.append(str(p))
    batches = tg._media_batches(paths, max_count=10, max_bytes=45)
    assert [len(b) for b in batches] == [2, 2]
    assert [p for b in batches for p in b] == paths
