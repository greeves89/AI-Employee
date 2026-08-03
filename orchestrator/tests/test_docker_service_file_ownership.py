"""Regression tests for issue #423: files written into agent containers must be
owned by the agent user (uid/gid 1000), not root, so the agent can update them
afterwards (e.g. /workspace/knowledge.md)."""
import io
import tarfile

from app.services.docker_service import DockerService


class _FakeContainer:
    def __init__(self):
        self.archives = []  # list of (dir_path, tar_bytes)

    def put_archive(self, dir_path, tar_stream):
        self.archives.append((dir_path, tar_stream.read()))
        return True


class _FakeClient:
    def __init__(self, container):
        self._container = container

    class _Containers:
        def __init__(self, container):
            self._container = container

        def get(self, _container_id):
            return self._container

    @property
    def containers(self):
        return self._Containers(self._container)


def _service_with_fake_container():
    container = _FakeContainer()
    svc = DockerService.__new__(DockerService)  # bypass __init__ (needs a live daemon)
    svc.client = _FakeClient(container)
    return svc, container


def _members(tar_bytes):
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        return list(tar.getmembers())


def test_write_file_in_container_owned_by_agent_uid():
    svc, container = _service_with_fake_container()
    svc.write_file_in_container("cid", "/workspace/knowledge.md", "hello")

    (_dir, tar_bytes) = container.archives[0]
    members = _members(tar_bytes)
    assert len(members) == 1
    assert members[0].name == "knowledge.md"
    assert members[0].uid == 1000
    assert members[0].gid == 1000


def test_write_files_in_container_owned_by_agent_uid():
    svc, container = _service_with_fake_container()
    svc.write_files_in_container(
        "cid", "/workspace", [("a.txt", b"a"), ("b.txt", b"bb")]
    )

    (_dir, tar_bytes) = container.archives[0]
    members = _members(tar_bytes)
    assert {m.name for m in members} == {"a.txt", "b.txt"}
    for m in members:
        assert m.uid == 1000
        assert m.gid == 1000


def test_ownership_override_is_honoured():
    svc, container = _service_with_fake_container()
    svc.write_file_in_container("cid", "/etc/thing", "x", uid=0, gid=0)

    (_dir, tar_bytes) = container.archives[0]
    members = _members(tar_bytes)
    assert members[0].uid == 0
    assert members[0].gid == 0
