"""collect91 job 的目录归属：默认落到「91porn」，不存在则创建。"""
from workers.scheduled.collect91.job import _folder_id


class FakeClient:
    def __init__(self, folders):
        self._folders = folders
        self.created = None

    def folders(self):
        return self._folders

    def create_folder(self, name):
        self.created = name
        return {"id": "new-folder-id", "name": name}


def test_uses_existing_folder():
    client = FakeClient([{"id": "f1", "name": "合集A"}, {"id": "f2", "name": "91porn"}])
    assert _folder_id(client) == "f2"
    assert client.created is None


def test_creates_folder_when_missing():
    client = FakeClient([{"id": "f1", "name": "合集A"}])
    assert _folder_id(client) == "new-folder-id"
    assert client.created == "91porn"
