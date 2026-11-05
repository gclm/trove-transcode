from workers.upload.job import _archive_name


def test_archive_name_uses_folder_and_title():
    assert _archive_name("电视剧", "第一集", "source.mp4") == "电视剧 - 第一集.mp4"


def test_archive_name_without_folder_keeps_title():
    assert _archive_name(None, "第一集", "source.mp4") == "第一集.mp4"


def test_archive_name_does_not_duplicate_known_extension():
    assert _archive_name("电视剧", "第一集.mp4", "source.mp4") == "电视剧 - 第一集.mp4"
