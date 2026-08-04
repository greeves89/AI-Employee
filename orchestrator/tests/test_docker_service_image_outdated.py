"""Tests for issue #433 Phase 2: detect agent containers running a stale image.

Agent containers are started from ai-employee-agent:latest but are not recreated
when that tag is rebuilt, so a live agent can silently serve old code. The API
surfaces this as an `image_outdated` flag driven by
DockerService.is_container_image_outdated."""
from app.services.docker_service import DockerService


class _FakeImage:
    def __init__(self, image_id):
        self.id = image_id


class _FakeContainer:
    def __init__(self, image_id):
        self.image = _FakeImage(image_id)


class _FakeClient:
    def __init__(self, tag_image_id, container_image_id, raise_on_tag=False,
                 raise_on_container=False):
        self._tag_image_id = tag_image_id
        self._container_image_id = container_image_id
        self._raise_on_tag = raise_on_tag
        self._raise_on_container = raise_on_container
        self.images_get_calls = 0

    class _Images:
        def __init__(self, outer):
            self._outer = outer

        def get(self, _image_name):
            self._outer.images_get_calls += 1
            if self._outer._raise_on_tag:
                raise RuntimeError("image not found")
            return _FakeImage(self._outer._tag_image_id)

    class _Containers:
        def __init__(self, outer):
            self._outer = outer

        def get(self, _container_id):
            if self._outer._raise_on_container:
                raise RuntimeError("no such container")
            return _FakeContainer(self._outer._container_image_id)

    @property
    def images(self):
        return self._Images(self)

    @property
    def containers(self):
        return self._Containers(self)


def _service(**kwargs):
    svc = DockerService.__new__(DockerService)  # bypass __init__ (needs a live daemon)
    svc.client = _FakeClient(**kwargs)
    return svc


def test_outdated_when_container_image_differs_from_tag():
    svc = _service(tag_image_id="sha256:new", container_image_id="sha256:old")
    assert svc.is_container_image_outdated("cid") is True


def test_not_outdated_when_ids_match():
    svc = _service(tag_image_id="sha256:same", container_image_id="sha256:same")
    assert svc.is_container_image_outdated("cid") is False


def test_not_outdated_when_tag_id_unknown():
    # Fail-closed: an unresolvable tag must never raise a false alarm.
    svc = _service(tag_image_id="x", container_image_id="y", raise_on_tag=True)
    assert svc.is_container_image_outdated("cid") is False


def test_not_outdated_when_container_id_unknown():
    svc = _service(tag_image_id="x", container_image_id="y", raise_on_container=True)
    assert svc.is_container_image_outdated("cid") is False


def test_current_image_id_skips_tag_lookup():
    # issue #449: when the caller already resolved the tag's image id (e.g. once
    # for a whole agent list), is_container_image_outdated must not call
    # images.get() again per container.
    svc = _service(tag_image_id="sha256:new", container_image_id="sha256:old")
    result = svc.is_container_image_outdated("cid", current_image_id="sha256:new")
    assert result is True
    assert svc.client.images_get_calls == 0


def test_current_image_id_none_still_resolves_via_tag_lookup():
    svc = _service(tag_image_id="sha256:same", container_image_id="sha256:same")
    result = svc.is_container_image_outdated("cid", current_image_id=None)
    assert result is False
    assert svc.client.images_get_calls == 1
