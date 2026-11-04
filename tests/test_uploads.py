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
