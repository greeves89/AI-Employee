"""Regression tests for issue #423: files written into agent containers must be
owned by the agent user (uid/gid 1000), not root, so the agent can update them
afterwards (e.g. /workspace/knowledge.md)."""
import io
import tarfile

from app.services.docker_service import DockerService


class _FakeContainer:
    def __init__(self):
        self.archives = []  # list of (dir_path, tar_bytes)
        self.execs = []

    def put_archive(self, dir_path, tar_stream):
        self.archives.append((dir_path, tar_stream.read()))
        return True

    def exec_run(self, _cmd, **_kwargs):
        # write_files_in_container bereitet den Zielordner seit #840 selbst vor
        # (ein exec im Behaelter). Hier soll er gelingen — dass er ueberhaupt
        # laeuft und was er ablehnt, prueft test_import_zielordner_symlink.py.
        self.execs.append(_cmd)
        return 0, (b"", b"")


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


# --- Nested names: the directories must belong to the agent too -------------
#
# The ZIP import hands write_files_in_container names like
# "meine-app/app/routes/download.py". put_archive creates every directory the
# archive does not carry itself — as root. Seen on an imported app: files
# uid 1000 (mtime 0), all directories root:root 0755. The agent could edit
# the imported files but not create one (Permission denied), and a rebuild
# with a new module crash-looped.


def test_nested_names_carry_agent_owned_directory_entries():
    svc, container = _service_with_fake_container()
    svc.write_files_in_container(
        "cid", "/workspace",
        [("meine-app/app/routes/download.py", b"x"), ("meine-app/app/main.py", b"y")],
    )

    (_dir, tar_bytes) = container.archives[0]
    members = _members(tar_bytes)
    dirs = [m for m in members if m.isdir()]
    assert [m.name for m in dirs] == ["meine-app", "meine-app/app", "meine-app/app/routes"]
    for m in dirs:
        assert (m.uid, m.gid, m.mode) == (1000, 1000, 0o755), m.name


def test_directory_entries_precede_their_files_and_appear_once():
    svc, container = _service_with_fake_container()
    svc.write_files_in_container(
        "cid", "/workspace", [("p/a.txt", b"a"), ("p/b.txt", b"b"), ("p/q/c.txt", b"c")]
    )

    (_dir, tar_bytes) = container.archives[0]
    names = [m.name for m in _members(tar_bytes)]
    assert names.count("p") == 1 and names.count("p/q") == 1
    assert names.index("p") < names.index("p/a.txt")
    assert names.index("p/q") < names.index("p/q/c.txt")


def test_flat_names_add_no_directory_entries():
    svc, container = _service_with_fake_container()
    svc.write_files_in_container("cid", "/workspace", [("a.txt", b"a")])

    (_dir, tar_bytes) = container.archives[0]
    assert [m.name for m in _members(tar_bytes)] == ["a.txt"]


def test_directory_entries_honour_the_ownership_override():
    svc, container = _service_with_fake_container()
    # Ziel bewusst unter /workspace: der Schreib-Helfer nimmt seit #840 nichts
    # anderes mehr an. Geprueft wird hier die uid/gid-Durchreichung.
    svc.write_files_in_container("cid", "/workspace/x", [("d/f", b"x")], uid=0, gid=0)

    (_dir, tar_bytes) = container.archives[0]
    d = next(m for m in _members(tar_bytes) if m.isdir())
    assert (d.uid, d.gid) == (0, 0)


def test_directory_entries_never_include_the_target_dir_itself_or_siblings():
    """Die Normalisierung (agent-owned 0755, auch fuer vorhandene Ordner) trifft
    nur Ordner AUF DEM PFAD geschriebener Dateien — nie ``target_dir`` selbst
    (kein Eintrag "" oder ".") und keine Geschwister."""
    svc, container = _service_with_fake_container()
    svc.write_files_in_container("cid", "/workspace/projects", [("app/src/x.py", b"x")])

    (_dir, tar_bytes) = container.archives[0]
    names = [m.name for m in _members(tar_bytes)]
    assert names == ["app", "app/src", "app/src/x.py"]
    assert "" not in names and "." not in names and "projects" not in names


def test_directory_entries_carry_a_mode_at_all():
    """Docker uebernimmt den Modus eines Verzeichniseintrags auch fuer ein
    vorhandenes Verzeichnis. Ein Eintrag OHNE gesetzten Modus (TarInfo-Default
    0o644 fuer DIRTYPE) machte den Ordner unbetretbar — der Modus muss
    explizit ausfuehrbar sein."""
    svc, container = _service_with_fake_container()
    svc.write_files_in_container("cid", "/workspace", [("d/f", b"x")])

    (_dir, tar_bytes) = container.archives[0]
    d = next(m for m in _members(tar_bytes) if m.isdir())
    assert d.mode & 0o111 == 0o111, oct(d.mode)
    assert d.mode == 0o755
