"""Object storage — tenant-prefixed keys, and the in-memory backend used by
everything that does not need a real MinIO (handoff section 4, D8)."""

from __future__ import annotations

import pytest

from app.services.storage import (
    InMemoryStorageBackend,
    StorageError,
    build_backend,
    new_object_name,
    tenant_key,
)


# ---------------------------------------------------------------------------
# tenant_key: the only sanctioned way to build an object key
# ---------------------------------------------------------------------------
def test_a_key_is_prefixed_with_the_tenant() -> None:
    assert tenant_key("acme", "images", "x.png") == "acme/images/x.png"


@pytest.mark.parametrize(
    "prefix",
    ["", "../acme", "acme/../other", "/acme", "acme\x00"],
)
def test_an_unsafe_prefix_is_refused(prefix: str) -> None:
    with pytest.raises(StorageError):
        tenant_key(prefix, "images", "x.png")


@pytest.mark.parametrize("segment", ["../escape", "a/../b", "..", ""])
def test_an_unsafe_segment_is_refused(segment: str) -> None:
    with pytest.raises(StorageError):
        tenant_key("acme", segment)


def test_a_key_needs_at_least_one_segment() -> None:
    with pytest.raises(StorageError):
        tenant_key("acme")


def test_leading_and_trailing_slashes_are_normalised() -> None:
    assert tenant_key("acme", "/images/", "/x.png/") == "acme/images/x.png"


def test_two_tenants_can_never_collide_on_the_same_key() -> None:
    """The prefix is always the first segment, so no path under one tenant's
    prefix can ever resolve to a key under another's."""
    a = tenant_key("acme", "packages", "1", "images", "x.png")
    b = tenant_key("globex", "packages", "1", "images", "x.png")
    assert a != b
    assert a.split("/", 1)[0] == "acme"
    assert b.split("/", 1)[0] == "globex"


def test_object_names_do_not_collide() -> None:
    names = {new_object_name("png") for _ in range(1000)}
    assert len(names) == 1000
    assert all(name.endswith(".png") for name in names)


# ---------------------------------------------------------------------------
# the in-memory backend
# ---------------------------------------------------------------------------
@pytest.fixture
def backend() -> InMemoryStorageBackend:
    return InMemoryStorageBackend()


def test_put_and_get_round_trip(backend: InMemoryStorageBackend) -> None:
    key = tenant_key("acme", "images", "x.png")
    stored = backend.put(key, b"\x89PNG...", "image/png")

    assert stored.key == key
    assert stored.size_bytes == len(b"\x89PNG...")
    assert stored.content_type == "image/png"
    assert backend.get(key) == b"\x89PNG..."


def test_content_type_is_guessed_when_not_given(backend: InMemoryStorageBackend) -> None:
    stored = backend.put(tenant_key("acme", "x.png"), b"data")
    assert stored.content_type == "image/png"


def test_an_unknown_extension_falls_back_to_octet_stream(
    backend: InMemoryStorageBackend,
) -> None:
    stored = backend.put(tenant_key("acme", "x.unknownext"), b"data")
    assert stored.content_type == "application/octet-stream"


def test_reading_a_missing_object_is_reported_clearly(
    backend: InMemoryStorageBackend,
) -> None:
    with pytest.raises(StorageError):
        backend.get(tenant_key("acme", "nope.png"))


def test_exists_reflects_what_was_written(backend: InMemoryStorageBackend) -> None:
    key = tenant_key("acme", "x.png")
    assert not backend.exists(key)
    backend.put(key, b"data")
    assert backend.exists(key)


def test_delete_removes_the_object(backend: InMemoryStorageBackend) -> None:
    key = tenant_key("acme", "x.png")
    backend.put(key, b"data")
    backend.delete(key)
    assert not backend.exists(key)
    with pytest.raises(StorageError):
        backend.get(key)


def test_deleting_a_missing_object_does_not_raise(
    backend: InMemoryStorageBackend,
) -> None:
    backend.delete(tenant_key("acme", "never-existed.png"))


def test_a_put_overwrites_the_previous_content(backend: InMemoryStorageBackend) -> None:
    key = tenant_key("acme", "x.png")
    backend.put(key, b"first")
    backend.put(key, b"second")
    assert backend.get(key) == b"second"


def test_list_objects_returns_every_key_under_a_prefix(
    backend: InMemoryStorageBackend,
) -> None:
    backend.put(tenant_key("acme", "images", "a.png"), b"1")
    backend.put(tenant_key("acme", "images", "b.png"), b"2")
    backend.put(tenant_key("globex", "images", "c.png"), b"3")

    assert set(backend.list_objects("acme/")) == {
        tenant_key("acme", "images", "a.png"),
        tenant_key("acme", "images", "b.png"),
    }
    assert set(backend.list_objects()) == {
        tenant_key("acme", "images", "a.png"),
        tenant_key("acme", "images", "b.png"),
        tenant_key("globex", "images", "c.png"),
    }


def test_list_objects_is_empty_for_an_unused_prefix(
    backend: InMemoryStorageBackend,
) -> None:
    assert backend.list_objects("nothing-here/") == []


def test_url_is_stable_but_never_a_real_http_address(
    backend: InMemoryStorageBackend,
) -> None:
    """Nothing should ever try to fetch this URL over the network; it exists
    only so callers have something in the ``url`` field during a test."""
    key = tenant_key("acme", "x.png")
    backend.put(key, b"data")
    url = backend.url(key)
    assert not url.startswith(("http://", "https://"))
    assert key in url


# ---------------------------------------------------------------------------
# picking a backend
# ---------------------------------------------------------------------------
def test_build_backend_defaults_to_in_memory() -> None:
    assert isinstance(build_backend("memory"), InMemoryStorageBackend)


def test_build_backend_picks_s3_when_asked() -> None:
    from app.services.storage import S3StorageBackend

    backend = build_backend("s3")
    assert isinstance(backend, S3StorageBackend)
