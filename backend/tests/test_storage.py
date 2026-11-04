"""Against a real S3-compatible server (SeaweedFS in the dev stack)."""

import urllib.request
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app.core import storage as storage_mod
from app.core.storage import ChecksumMismatch, ObjectExists, sha256_file


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_mutable_round_trip_verifies_hash(storage_as, key_prefix, tmp_path):
    work = storage_as("worker", "work")
    src = write(tmp_path, "a.bin", b"hello")
    info = work.put_file(f"{key_prefix}/a.bin", src, "application/octet-stream")
    assert info.sha256 == sha256_file(src)
    head = work.head(f"{key_prefix}/a.bin")
    assert head and head.sha256 == info.sha256 and head.size == 5
    dest = tmp_path / "out" / "a.bin"
    work.get_file(f"{key_prefix}/a.bin", dest)
    assert dest.read_bytes() == b"hello"


def test_tampered_object_is_detected(storage_as, key_prefix, tmp_path):
    work = storage_as("worker", "work")
    src = write(tmp_path, "a.bin", b"hello")
    work.put_file(f"{key_prefix}/a.bin", src, "application/octet-stream", sha256="0" * 64)  # lie about it
    with pytest.raises(ChecksumMismatch):
        work.get_file(f"{key_prefix}/a.bin", tmp_path / "x.bin")
    assert not (tmp_path / "x.bin").exists()
    with pytest.raises(ChecksumMismatch):
        work.get_file(f"{key_prefix}/a.bin", tmp_path / "y.bin", expected_sha256="1" * 64)


def test_put_immutable_writes_once(storage_as, key_prefix, tmp_path):
    lib = storage_as("publisher", "library")
    key = f"{key_prefix}/versions/1.0/character.blend"
    first = write(tmp_path, "v1", b"version one")
    lib.put_immutable(key, first, "application/x-blender", sha256_file(first))
    # identical content again: a resumed publish, accepted
    lib.put_immutable(key, first, "application/x-blender", sha256_file(first))
    other = write(tmp_path, "v2", b"something else")
    with pytest.raises(ObjectExists):
        lib.put_immutable(key, other, "application/x-blender", sha256_file(other))
    assert lib.get_bytes(key) == b"version one"


def test_conditional_write_refused_by_the_server(storage_as, key_prefix, tmp_path):
    """The race the HEAD check cannot close: the server itself must refuse the second write."""
    lib = storage_as("publisher", "library")
    key = f"{key_prefix}/race.bin"
    a = write(tmp_path, "a", b"A")
    lib._upload(key, a, "application/octet-stream", sha256_file(a), conditional=True)
    b = write(tmp_path, "b", b"B")
    with pytest.raises(ObjectExists):
        lib._upload(key, b, "application/octet-stream", sha256_file(b), conditional=True)
    assert lib.get_bytes(key) == b"A"


def test_multipart_immutable_upload(storage_as, key_prefix, tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "CHUNK", 5 * 1024 * 1024)  # S3 minimum part size
    lib = storage_as("publisher", "library")
    data = bytes(range(256)) * (11 * 1024 * 1024 // 256 + 1)
    big = write(tmp_path, "big", data)
    key = f"{key_prefix}/big.bin"
    lib.put_immutable(key, big, "application/octet-stream", sha256_file(big))
    lib.get_file(key, tmp_path / "back")
    assert sha256_file(tmp_path / "back") == sha256_file(big)
    small = write(tmp_path, "small", b"x")
    with pytest.raises(ObjectExists):
        lib.put_immutable(key, small, "application/octet-stream", sha256_file(small))
    # a concurrent multipart over the same key is refused by the server too
    with pytest.raises(ObjectExists):
        lib._upload(key, big, "application/octet-stream", sha256_file(big), conditional=True)


def test_declared_hash_must_match_the_file(storage_as, key_prefix, tmp_path):
    lib = storage_as("publisher", "library")
    f = write(tmp_path, "f", b"data")
    with pytest.raises(ChecksumMismatch):
        lib.put_immutable(f"{key_prefix}/f", f, "application/octet-stream", "2" * 64)


@pytest.mark.parametrize("identity", ["worker", "api"])
def test_only_the_publisher_can_write_the_library(storage_as, key_prefix, tmp_path, identity):
    f = write(tmp_path, "f", b"data")
    lib = storage_as(identity, "library")
    with pytest.raises(ClientError) as err:
        lib.put_immutable(f"{key_prefix}/f", f, "application/octet-stream", sha256_file(f))
    assert err.value.response["Error"]["Code"] == "AccessDenied"
    with pytest.raises(ClientError):
        lib.client.delete_object(Bucket=lib.bucket, Key=f"{key_prefix}/f")


def test_publisher_cannot_write_work(storage_as, key_prefix, tmp_path):
    f = write(tmp_path, "f", b"data")
    with pytest.raises(ClientError):
        storage_as("publisher", "work").put_file(f"{key_prefix}/f", f, "application/octet-stream")


def test_presigned_urls(storage_as, key_prefix, tmp_path):
    work = storage_as("api", "work")
    key = f"{key_prefix}/upload.png"
    put_url = work.presign_put(key, "image/png", expires_s=60)
    req = urllib.request.Request(put_url, data=b"\x89PNG fake", method="PUT", headers={"Content-Type": "image/png"})
    assert urllib.request.urlopen(req).status == 200
    get_url = work.presign_get(key, filename="upload.png", expires_s=60)
    assert urllib.request.urlopen(get_url).read() == b"\x89PNG fake"


@pytest.mark.parametrize("key", ["", "/abs", "a/../b", "a//b", "a\\b", "./a"])
def test_unsafe_keys_rejected(storage_as, key):
    with pytest.raises(ValueError):
        storage_as("worker", "work").head(key)
