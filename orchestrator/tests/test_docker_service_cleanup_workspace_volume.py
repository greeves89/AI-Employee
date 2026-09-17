"""Issue #714, Punkt 1: ein Disk-Quota-gestoppter Agent hatte keinen Weg
zurueck — Aufraeumen brauchte einen laufenden Container, und genau den
verhinderte der Zustand, der aufgeraeumt werden musste.

``cleanup_workspace_volume`` raeumt direkt im Docker-VOLUME auf (gleiches
Muster wie ``copy_workspace_volume``: ein kurzlebiger Helfer-Container, der
NUR das Volume mountet) — braucht den Agenten-Container selbst nicht.
"""
from app.services.docker_service import DockerService


class _FakeContainersApi:
    def __init__(self, output=b"42\n", raise_on_run=False):
        self._output = output
        self._raise_on_run = raise_on_run
        self.run_calls = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)
        if self._raise_on_run:
            raise RuntimeError("docker daemon unreachable")
        return self._output


class _FakeClient:
    def __init__(self, **kwargs):
        self.containers = _FakeContainersApi(**kwargs)


def _service(**kwargs):
    svc = DockerService.__new__(DockerService)  # bypass __init__ (needs a live daemon)
    svc.client = _FakeClient(**kwargs)
    return svc


def test_returns_the_workspace_size_after_cleanup():
    svc = _service(output=b"42\n")
    assert svc.cleanup_workspace_volume("workspace-a1") == 42


def test_mounts_only_the_volume_not_the_agent_container():
    # Der ganze Witz des Fixes: kein container_id noetig, nur der Volume-Name.
    svc = _service()
    svc.cleanup_workspace_volume("workspace-a1")
    kwargs = svc.client.containers.run_calls[0]
    assert kwargs["volumes"] == {"workspace-a1": {"bind": "/workspace", "mode": "rw"}}
    assert kwargs["remove"] is True


def test_the_cleanup_script_clears_caches_tmp_and_logs():
    svc = _service()
    svc.cleanup_workspace_volume("workspace-a1")
    script = svc.client.containers.run_calls[0]["command"][-1]
    assert "/workspace/data/cache" in script
    assert "/workspace/tmp" in script
    assert "*.log" in script


def test_a_failed_helper_run_returns_none_not_raises():
    svc = _service(raise_on_run=True)
    assert svc.cleanup_workspace_volume("workspace-a1") is None


def test_unparseable_output_returns_none():
    svc = _service(output=b"")
    assert svc.cleanup_workspace_volume("workspace-a1") is None
